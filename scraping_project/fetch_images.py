"""ページのURLを指定して、載っている画像を指定フォルダに保存する

使い方: python fetch_images.py URL 保存フォルダ [--min-side 300]

    ・サムネイルがリンク先の原寸画像へのリンクなら、原寸を保存する
    ・短辺が --min-side 未満の画像（アイコン・バナー等）は保存しない
    ・robots.txtで禁止されているページは取得しない。403・429が返ったら回避せずに止める
    ・保存した画像は 0001.jpg のように連番で、元ページと画像ごとのURL・出典は 取得元.txt に残る
    ・--by-source を付けると、記事に出典が書かれた画像を「出典 102巻」のようなサブフォルダに分ける
    ・--skip-overlay を付けると、白黒のコマに色付きの文字等を重ねた加工画像は保存しない（カラーページは残す）
    ・取得した画像の権利は権利者にある。使い方は利用者の責任で判断する
"""
import argparse
import io
import os
import re
import time
import unicodedata
from datetime import date
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageChops

REQUEST_INTERVAL_SEC = 2
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
# 記事内の商品リンクの画像（単行本の表紙等）は保存しない
AD_IMAGE_HOSTS = ("media-amazon.com", "images-amazon.com", "thumbnail.image.rakuten.co.jp")
# 会話形式の吹き出しのアイコン、筆者紹介、商品紹介の中の画像は記事の画像ではない
NOT_CONTENT_CLASS = re.compile(r"avatar|speech|balloon|author|profile|commerce|product|goods|affiliate", re.IGNORECASE)
# 出典の書き方（「出典:◯◯」「引用：◯◯」「© 作者 ◯◯話より」）
SOURCE_TEXT = re.compile(r"^\s*(?:画像)?(?:出典|引用)|^\s*(?:©|\(c\)|（c）)|より\s*$", re.IGNORECASE)
# 白黒のコマに色付きの文字等を重ねた加工の判定。白黒の漫画は鮮やかな色の画素（RGBの差がVIVID_CHROMA以上）が0で、
# 加工した画像は色の付いた画素（差がCOLOR_CHROMA以上）が文字の部分だけ、かつ文字の1〜2色だけになる。
# カラーページは色が画像全体に広がるか、背景が白くても肌・服・髪などいろいろな色が混ざる
COLOR_CHROMA = 40
VIVID_CHROMA = 80
MIN_OVERLAY_VIVID_RATIO = 0.001
MAX_OVERLAY_COLOR_RATIO = 0.10
HUE_BINS = 12
MIN_OVERLAY_TOP2_HUE_SHARE = 0.95


def main(url, folder, min_side=300, by_source=False, skip_overlay=False):
    """画像を保存し、保存したファイルのパスのリストを返す

    by_source=True なら、記事に出典（「出典:ONE PIECE 102巻」等）が書かれた画像を「出典 102巻」のようなサブフォルダに分ける
    skip_overlay=True なら、白黒のコマに色付きの文字やマークを重ねた加工画像は保存しない
    """
    if not allowed_by_robots(url):
        raise PermissionError(f"robots.txtで禁止されているため取得しません: {url}")
    response = requests.get(url, timeout=30)
    if response.status_code in (403, 429):
        raise PermissionError(f"サイトに拒否されました(HTTP {response.status_code})。回避はしません: {url}")
    response.raise_for_status()

    os.makedirs(folder, exist_ok=True)
    saved = []
    log = []
    for src, source in find_images(BeautifulSoup(response.text, "html.parser"), url):
        time.sleep(REQUEST_INTERVAL_SEC)
        try:
            image_response = requests.get(src, timeout=30, headers={"Referer": url})
        except requests.RequestException:
            continue
        if image_response.status_code in (403, 429):
            raise PermissionError(f"サイトに拒否されました(HTTP {image_response.status_code})。回避はしません: {src}")
        extension = image_extension(image_response, min_side)
        if extension and skip_overlay and has_color_overlay(image_response.content):
            log.append(f"  除外（色付きの文字等の加工）  {src}")
        elif extension:
            target = os.path.join(folder, f"出典 {source_label(source)}") if by_source and source else folder
            os.makedirs(target, exist_ok=True)
            path = os.path.join(target, f"{next_number(target):04d}{extension}")
            with open(path, "wb") as f:
                f.write(image_response.content)
            saved.append(path)
            log.append(f"  {os.path.relpath(path, folder)}  {src}" + (f"  （{source}）" if source else ""))
            print(f"保存: {path}  {src}")

    with open(os.path.join(folder, "取得元.txt"), "a", encoding="utf-8") as f:
        f.write(f"{date.today()} {url} {len(saved)}枚\n" + "".join(line + "\n" for line in log))
    print(f"{len(saved)}枚を保存しました: {folder}")
    return saved


def next_number(folder):
    """同じフォルダに続けて保存しても上書きしないよう、既存の番号の続きを返す"""
    numbers = [int(name[:4]) for name in os.listdir(folder) if name[:4].isdigit() and name.lower().endswith(IMAGE_EXTENSIONS)]
    return max(numbers, default=0) + 1


def allowed_by_robots(url):
    parts = urlparse(url)
    parser = robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        parser.read()
    except OSError:
        return True
    return parser.can_fetch("*", url)


def find_image_urls(soup, base_url):
    """記事本文の画像URLを出現順に返す"""
    return [url for url, _ in find_images(soup, base_url)]


def find_images(soup, base_url):
    """記事本文の画像を (URL, 出典の文 または None) で出現順に返す。画像へのリンクがあれば原寸を優先する"""
    # 関連記事のサムネイル等が混ざらないよう、本文があれば本文だけを見る
    body = soup.select_one(".entry-content") or soup.select_one("article") or soup
    images = {}
    for tag in body.find_all("img"):
        if not_content(tag, body):
            continue
        link = tag.find_parent("a", href=True)
        if link and link["href"].lower().split("?")[0].endswith(IMAGE_EXTENSIONS):
            src = link["href"]
        else:
            # 縮小版を表示して原寸をsrcsetにだけ書くサイトがあるので、srcsetがあれば一番大きい画像を使う
            srcset = tag.get("data-srcset") or tag.get("srcset")
            src = largest_in_srcset(srcset) if srcset else None
            src = src or next((tag.get(name) for name in ("data-src", "data-lazy-src", "data-original", "src") if tag.get(name)), None)
        if src and not src.startswith("data:"):
            images.setdefault(urljoin(base_url, src), image_source(tag))
    return [(url, source) for url, source in images.items() if not urlparse(url).hostname.endswith(AD_IMAGE_HOSTS)]


def not_content(tag, body):
    """本文の中で、吹き出しのアイコンや商品紹介の枠に入った画像か（本文より外側のclassは見ない）"""
    for parent in tag.parents:
        if parent is body:
            return False
        if NOT_CONTENT_CLASS.search(" ".join(parent.get("class", []))):
            return True
    return False


def image_source(tag):
    """画像のキャプション、または画像の直後の行に書かれた出典を返す。なければNone"""
    figure = tag.find_parent("figure")
    caption = figure.find("figcaption") if figure else None
    if caption:
        text = caption.get_text(" ", strip=True)
    else:
        block = tag.find_parent(["p", "figure", "div"])
        sibling = block.find_next_sibling() if block else None
        text = sibling.get_text(" ", strip=True) if sibling else ""
    return text if text and len(text) <= 80 and SOURCE_TEXT.search(text) else None


def source_label(source):
    """出典の文からフォルダ名にする部分（「102巻」「1197話」等）を取り出す"""
    source = unicodedata.normalize("NFKC", source)
    matched = re.search(r"(\d+)\s*巻", source) or re.search(r"(\d+)\s*話", source)
    if matched:
        return matched.group(0).replace(" ", "")
    label = SOURCE_TEXT.sub("", source).strip(" :：")
    return re.sub(r'[\\/:*?"<>|]', "_", label)[:30] or "不明"


def largest_in_srcset(srcset):
    """「URL 225w, URL 600w」形式から幅が最大のURLを返す"""
    candidates = []
    for part in srcset.split(","):
        words = part.split()
        if words and not words[0].startswith("data:"):
            width = words[1][:-1] if len(words) > 1 and words[1].endswith("w") else "0"
            candidates.append((int(width) if width.isdigit() else 0, words[0]))
    return max(candidates)[1] if candidates else None


def image_extension(response, min_side):
    """画像として使えるなら拡張子を返す"""
    if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
        return None
    try:
        with Image.open(io.BytesIO(response.content)) as image:
            if min(image.size) < min_side:
                return None
            return FORMAT_EXTENSIONS.get(image.format)
    except OSError:
        return None


def has_color_overlay(content):
    """白黒のコマに色付きの文字やマークを重ねた加工画像か

    鮮やかな色が画像の一部に1〜2色だけあれば、白黒の上に色を足したものとみなす。
    電子版等のカラーページは残る。カラー画像に重ねた文字は判別できない
    """
    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGB").resize((300, 300))
    red, green, blue = image.split()
    chroma = list(ImageChops.lighter(ImageChops.lighter(ImageChops.difference(red, green), ImageChops.difference(green, blue)),
                                     ImageChops.difference(red, blue)).getdata())
    vivid_hues = [hue * HUE_BINS // 256 for hue, value in zip(image.convert("HSV").getchannel("H").getdata(), chroma)
                  if value >= VIVID_CHROMA]
    if len(vivid_hues) / len(chroma) <= MIN_OVERLAY_VIVID_RATIO:
        return False
    if sum(value >= COLOR_CHROMA for value in chroma) / len(chroma) > MAX_OVERLAY_COLOR_RATIO:
        return False
    counts = sorted(vivid_hues.count(hue_bin) for hue_bin in range(HUE_BINS))
    return sum(counts[-2:]) / len(vivid_hues) >= MIN_OVERLAY_TOP2_HUE_SHARE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ページの画像を指定フォルダに保存する")
    parser.add_argument("url")
    parser.add_argument("folder")
    parser.add_argument("--min-side", type=int, default=300, help="これより小さい画像は保存しない(px)")
    parser.add_argument("--by-source", action="store_true", help="出典が書かれた画像を出典ごとのサブフォルダに分ける")
    parser.add_argument("--skip-overlay", action="store_true", help="白黒のコマに色付きの文字等を重ねた加工画像は保存しない")
    args = parser.parse_args()
    main(args.url, args.folder, args.min_side, args.by_source, args.skip_overlay)
