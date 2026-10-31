"""話数感想スレから反応集動画用の台本CSV・コマ画像を作成する

処理の流れ
    1. スレを読み込む（URLから取得、またはブラウザで保存したHTML）
    2. レスを選別し、話題ごとにまとめる
    3. スレに貼られた画像をコマとして自動収集し、レスに割り当てる
    4. 台本CSV(音声名, セリフ, コマ画像, 話題)と設定(.meta.json)を出力

コマ画像列は後から書き換えられる（「コマ画像」フォルダからの相対パス）。空欄なら話題名を表示する
URL取得が拒否された場合は回避せず、ブラウザで保存したHTMLを使う

あにまん掲示板のコンテンツ使用規約 https://bbs.animanch.com/board/905570/ に従う
    ・過去ログのスレのみ使用可能（現行スレ・削除スレは不可）
    ・創作イラストの転載禁止
    ・YouTube: 説明文の先頭に元スレURLを全て記載、動画内の先頭に掲示板名を記載
"""
import csv
import os
import re
import shutil
import time
from collections import Counter
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup, Comment
from PIL import Image

from .create_animan_video import PANEL_DIR, SCRIPT_DIR, safe_file_name, write_meta

SAVED_DIR = "保存スレ"

ARCHIVE_NOTICE = "このスレッドは過去ログ倉庫に格納されています"

# 読み上げ速度1.2で約9分になる文字数の目安
MAX_TOTAL_CHARS = 3400
MIN_RES_CHARS = 4
MAX_RES_CHARS = 200
MAX_TOPICS = 6
MIN_TOPIC_RES = 3
MIN_PANEL_SIDE = 200
REQUEST_INTERVAL_SEC = 3

IMAGE_LINK = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)|/img/", re.IGNORECASE)
URL_TEXT = re.compile(r"https?://\S+")
# 創作イラストの可能性があるレスの画像は収集しない
ILLUSTRATION_TEXT = re.compile(r"描いた|描いて|描きました|かいた絵|イラスト|落書き|らくがき|ファンアート|自作|生成|AI", re.IGNORECASE)
KEYWORD = re.compile(r"[ァ-ヶー]{3,}|[一-龥]{2,}")
STOP_WORDS = {"感想", "今回", "今週", "来週", "最新", "最後", "最初", "読者", "漫画", "作者", "展開", "本当", "自分", "個人的"}


def main(urls, saved_files, theme, copyright_text, hashtags, title_voice, narration_voices):
    """台本を作成し、(CSVファイル名, 警告リスト)を返す"""
    if not urls and not saved_files:
        raise ValueError("スレURLか保存したHTMLを指定してください")
    if not narration_voices:
        raise ValueError("読み上げ音声を選択してください")

    warnings = []
    threads = []
    for index, url in enumerate(urls):
        if index:
            time.sleep(REQUEST_INTERVAL_SEC)
        threads.append(fetch_thread(url))
    for name in saved_files:
        threads.append(load_saved_thread(name))

    res_list = []
    for thread_index, thread in enumerate(threads):
        check_archived(thread)
        res_list += parse_res_list(thread, thread_index)

    theme = theme.strip() or threads[0]["title"]
    title = f"【{theme}】に対する読者の反応集"
    topics = group_by_topic(select_res(res_list))
    if not topics:
        raise ValueError("使用できるレスがありませんでした")

    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    panels = collect_panels(res_list, topics, panel_dir, threads, warnings)

    rows = make_rows(theme, topics, panels, title_voice, narration_voices)
    meta = {
        "title": title,
        "theme": theme,
        "copyright": copyright_text.strip(),
        "hashtags": hashtags.strip(),
        "source_urls": [thread["url"] for thread in threads],
    }
    csv_name = output_script(title, rows, meta)
    if panels:
        warnings.append(f"コマを{len(set(panels.values()))}枚収集しました。創作イラストは規約で転載禁止のため、"
                        f"公開前に「{panel_dir}」を確認し、該当する画像は台本から外してください")
    else:
        warnings.append("コマ画像を収集できませんでした。台本のコマ画像列に後から指定できます")
    print(f"台本作成完了しました: {csv_name}")
    return csv_name, warnings


def list_saved_threads():
    """保存スレフォルダのHTML一覧（新しい順）"""
    if not os.path.isdir(SAVED_DIR):
        return []
    files = [f for f in os.listdir(SAVED_DIR) if f.lower().endswith((".html", ".htm"))]
    return sorted(files, key=lambda f: os.path.getmtime(os.path.join(SAVED_DIR, f)), reverse=True)


# ---------------------------------------------------------------- スレ読み込み

def fetch_thread(url):
    response = requests.get(url, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    if response.status_code != 200 or soup.find(id="reslist") is None:
        raise RuntimeError(
            f"スレを取得できませんでした(HTTP {response.status_code})。ブラウザでスレを開き、"
            f"「ウェブページ、完全」で「{SAVED_DIR}」フォルダに保存して指定してください: {url}")
    return make_thread(soup, url, None)


def load_saved_thread(name):
    path = os.path.join(SAVED_DIR, os.path.basename(name))
    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(id="reslist") is None:
        raise ValueError(f"スレのHTMLではありません: {name}")
    url = find_source_url(soup)
    if not url:
        raise ValueError(f"元スレURLが見つかりません。ブラウザの保存機能で保存してください: {name}")
    return make_thread(soup, url, os.path.abspath(SAVED_DIR))


def find_source_url(soup):
    """ブラウザが保存時に残すURLを探す（規約により説明文へ正しいURLを記載するため必須）"""
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        match = re.search(r"saved from url=\(\d+\)(\S+)", comment)
        if match:
            return match.group(1)
    canonical = soup.find("link", rel="canonical")
    return canonical.get("href") if canonical else None


def make_thread(soup, url, base_dir):
    title_tag = soup.find(id="threadTitle")
    return {"soup": soup, "url": url, "base_dir": base_dir, "title": title_tag.get_text().strip() if title_tag else ""}


def check_archived(thread):
    """規約により過去ログのスレのみ使用できる"""
    if ARCHIVE_NOTICE not in thread["soup"].get_text():
        raise ValueError(f"過去ログに入っていないスレは使用できません: {thread['url']}")


def parse_res_list(thread, thread_index):
    res_list = []
    for li in thread["soup"].find(id="reslist").find_all("li"):
        number_tag = li.find(class_="resnumber")
        if number_tag is None:
            continue
        # .replyにはこのレスへの返信が並ぶ
        replied = len(li.select(".reply a"))
        for anchor in li.select("p a.reslink"):
            anchor.extract()
        lines = [p.get_text(separator=" ") for p in li.find_all("p")]
        if "このレスは削除されています" in lines:
            continue
        res_list.append({
            "thread": thread_index,
            "no": int(number_tag.get_text()),
            "text": URL_TEXT.sub("", " ".join(lines)).strip(),
            "replied": replied,
            "images": [] if ILLUSTRATION_TEXT.search(" ".join(lines)) else find_images(li),
        })
    return res_list


def find_images(li):
    """レス内の画像候補を返す。サムネイルより先にリンク先の原寸を見る"""
    images = []
    for link in li.find_all("a", href=True):
        if IMAGE_LINK.search(link["href"]):
            images.append(link["href"])
    for tag in li.find_all("img"):
        src = tag.get("data-src") or tag.get("data-original") or tag.get("src")
        if src and not src.startswith("data:"):
            images.append(src)
    return list(dict.fromkeys(images))


# ---------------------------------------------------------------- レス選別

def select_res(res_list):
    """返信の多いレスを優先し、動画の長さに収まるだけ選ぶ"""
    candidates = [res for res in res_list
                  if res["no"] > 1 and MIN_RES_CHARS <= len(res["text"]) <= MAX_RES_CHARS]
    candidates.sort(key=lambda res: (-res["replied"], res["thread"], res["no"]))

    selected = []
    total = 0
    for res in candidates:
        if total + len(res["text"]) > MAX_TOTAL_CHARS:
            continue
        selected.append(res)
        total += len(res["text"])
    return selected


def res_keywords(res):
    return {word for word in KEYWORD.findall(res["text"]) if word not in STOP_WORDS}


def group_by_topic(res_list):
    """多くのレスに出てくる語で話題を分ける。[(話題名, レス一覧)]を、レス数の多い順で返す"""
    remaining = list(res_list)
    topics = []
    while remaining and len(topics) < MAX_TOPICS:
        counts = Counter(word for res in remaining for word in res_keywords(res))
        if not counts:
            break
        keyword, count = counts.most_common(1)[0]
        if count < MIN_TOPIC_RES:
            break
        topics.append((keyword, [res for res in remaining if keyword in res_keywords(res)]))
        remaining = [res for res in remaining if keyword not in res_keywords(res)]
    topics.sort(key=lambda topic: -len(topic[1]))
    if remaining:
        topics.append(("その他", remaining))
    # 話題内は投稿順
    return [(name, sorted(items, key=lambda res: (res["thread"], res["no"]))) for name, items in topics]


# ---------------------------------------------------------------- コマ収集

def collect_panels(res_list, topics, panel_dir, threads, warnings):
    """各レスに直前の画像を割り当てて保存し、{(スレ番号, レス番号): コマ画像の相対パス}を返す"""
    image_res = [res for res in res_list if res["images"]]
    assigned = {}
    for _, items in topics:
        for res in items:
            before = [r for r in image_res if r["thread"] == res["thread"] and r["no"] <= res["no"]]
            if before:
                assigned[(res["thread"], res["no"])] = before[-1]

    saved = {}
    panels = {}
    for key, source_res in assigned.items():
        source_key = (source_res["thread"], source_res["no"])
        if source_key not in saved:
            saved[source_key] = store_panel(source_res, threads[source_res["thread"]], panel_dir, warnings)
        if saved[source_key]:
            panels[key] = saved[source_key]
    return panels


def store_panel(res, thread, panel_dir, warnings):
    os.makedirs(panel_dir, exist_ok=True)
    # 保存済みHTMLなら手元の画像を先に試す
    sources = sorted(res["images"], key=lambda src: src.startswith("http")) if thread["base_dir"] else res["images"]
    for src in sources:
        temp_path = os.path.join(panel_dir, f"{res['thread']}_{res['no']:04d}.tmp")
        if not load_image_file(src, thread, temp_path):
            continue
        extension = image_extension(temp_path)
        if extension:
            path = os.path.join(panel_dir, f"{res['thread']}_{res['no']:04d}{extension}")
            os.replace(temp_path, path)
            return os.path.relpath(path, PANEL_DIR).replace("\\", "/")
        os.remove(temp_path)
    warnings.append(f"レス{res['no']}の画像を保存できませんでした")
    return None


def load_image_file(src, thread, dest):
    """保存済みHTML内の画像をコピーするか、URLから取得する"""
    if thread["base_dir"] and not src.startswith(("http://", "https://")):
        path = os.path.realpath(os.path.join(thread["base_dir"], unquote(src.split("?")[0])))
        if path.startswith(os.path.realpath(thread["base_dir"]) + os.sep) and os.path.isfile(path):
            shutil.copyfile(path, dest)
            return True
        return False
    try:
        time.sleep(REQUEST_INTERVAL_SEC)
        response = requests.get(urljoin(thread["url"], src), timeout=30)
    except requests.RequestException:
        return False
    if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
        return False
    with open(dest, "wb") as f:
        f.write(response.content)
    return True


def image_extension(path):
    """コマとして使える画像なら拡張子を返す（小さいアイコン等は除外）"""
    try:
        with Image.open(path) as image:
            if min(image.size) < MIN_PANEL_SIDE:
                return None
            return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(image.format)
    except OSError:
        return None


# ---------------------------------------------------------------- 出力

def make_rows(theme, topics, panels, title_voice, narration_voices):
    """[音声, セリフ, コマ画像, 話題]の行を作る。冒頭と締めは定型文"""
    first_panel = next((panels[key] for key in panels), "")
    rows = [
        [title_voice, f"今回は「{theme}」に対する読者の反応を紹介します！", first_panel, ""],
        [title_voice, "この動画はネタバレを含みますのでご注意ください。チャンネル登録、高評価よろしくお願いします！それではどうぞ！",
         first_panel, ""],
    ]
    count = 0
    last_panel = first_panel
    for topic, items in topics:
        for res in items:
            last_panel = panels.get((res["thread"], res["no"]), "")
            rows.append([narration_voices[count % len(narration_voices)], res["text"], last_panel, topic])
            count += 1
    rows.append([title_voice, "これで以上になります。次回の動画も楽しみにしていてください！", last_panel, ""])
    return rows


def output_script(title, rows, meta):
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    csv_name = safe_file_name(title) + ".csv"
    csv_path = os.path.join(SCRIPT_DIR, csv_name)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    write_meta(csv_path, meta)
    return csv_name
