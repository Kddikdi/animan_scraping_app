"""話数感想スレから反応集動画用の台本CSV・コマ画像を作成する

処理の流れ
    1. スレを読み込む（URLから取得、またはブラウザで保存したHTML）
    2. レスを選別し、話題ごとにまとめる（キャラの別名は感想サイト/data.jsonのcharactersを使う）。
       絵文字だけ・元のレスがないと意味が通らないレスは外し、返信は返信先と一緒に並べる。読み上げ用に表記をそろえる
    3. スレに貼られた漫画の切り抜きをコマとして自動収集し、関連するレスだけに割り当てる
    4. 台本CSV(音声名, セリフ, コマ画像, 話題)と設定(.meta.json)を出力

コマ画像列は後から書き換えられる（「コマ画像」フォルダからの相対パス）。空欄なら話題名を表示する
URL取得が拒否された場合は回避せず、ブラウザで保存したHTMLを使う

あにまん掲示板のコンテンツ使用規約 https://bbs.animanch.com/board/905570/ に従う
    ・過去ログのスレのみ使用可能（現行スレ・削除スレは不可）
    ・創作イラストの転載禁止
    ・YouTube: 説明文の先頭に元スレURLを全て記載、動画内の先頭に掲示板名を記載
"""
import csv
import json
import os
import re
import shutil
import time
import unicodedata
from collections import Counter
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup, Comment
from PIL import Image, ImageChops

from .create_animan_video import PANEL_DIR, SCRIPT_DIR, safe_file_name, write_meta
from .find_review_sites import load_works
from .image_reading import compact

SAVED_DIR = "保存スレ"
# スレの画像ごとの手がかり（画像を貼ったレスと、その返信の文章）。動画のコマ割り当てで使う
THREAD_IMAGE_INFO_FILE = "スレ画像情報.json"

ARCHIVE_NOTICE = "このスレッドは過去ログ倉庫に格納されています"

# 読み上げ速度1.2で約9分になる文字数の目安
MAX_TOTAL_CHARS = 3400
MIN_RES_CHARS = 4
MAX_RES_CHARS = 200
MAX_TOPICS = 6
MIN_TOPIC_RES = 3
MIN_PANEL_SIDE = 200
# 色の付いた画素がこの割合を超える画像はカラーイラスト等とみなしてコマにしない
PANEL_CHROMA = 40
MAX_PANEL_COLOR_RATIO = 0.05
REQUEST_INTERVAL_SEC = 3

IMAGE_LINK = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)|/img/", re.IGNORECASE)
URL_TEXT = re.compile(r"https?://\S+")
# 創作イラストの可能性があるレスの画像は収集しない
ILLUSTRATION_TEXT = re.compile(r"描いた|描いて|描きました|かいた絵|イラスト|落書き|らくがき|ファンアート|自作|生成|AI", re.IGNORECASE)
KEYWORD = re.compile(r"[ァ-ヶー]{3,}|[一-龥]{2,}")
STOP_WORDS = {"感想", "今回", "今週", "来週", "最新", "最後", "最初", "読者", "漫画", "作者", "展開", "本当", "自分", "個人的"}
# 読み上げにくい文字（絵文字、「草www」の笑い）
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
LAUGH = re.compile(r"(?<=[^\sA-Za-z])[wW]+(?=\s|$)|[wW]{2,}")
CONTENT_CHAR = re.compile(r"[ぁ-んァ-ヶ一-龥A-Za-z0-9]")
# 元のレスがないと意味が通らない短いレスの書き出し
CONTEXT_START = re.compile(r"^(それ|これ|あれ|そう|そこ|だよね|ほんと|確かに|わかる|分かる|いや|まあ|だな|だね)")
CONTEXT_MAX_CHARS = 25


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
    topics = group_by_topic(select_res(res_list), work_characters(theme))
    if not topics:
        raise ValueError("使用できるレスがありませんでした")

    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    panels, links = collect_panels(res_list, topics, panel_dir, threads, warnings)

    rows = make_rows(theme, topics, panels, links, title_voice, narration_voices)
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
        # .replyにはこのレスへの返信、本文の>>番号には返信先が並ぶ
        replies = [int(number) for link in li.select(".reply a") for number in re.findall(r"\d+", link.get_text())]
        parents = []
        for anchor in li.select("p a.reslink"):
            parents += [int(number) for number in re.findall(r"\d+", anchor.get_text())]
            anchor.extract()
        lines = [p.get_text(separator=" ") for p in li.find_all("p")]
        if "このレスは削除されています" in lines:
            continue
        res_list.append({
            "thread": thread_index,
            "no": int(number_tag.get_text()),
            "text": clean_text(URL_TEXT.sub("", " ".join(lines))),
            "replied": len(replies),
            "replies": replies,
            "parents": parents,
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

def clean_text(text):
    """読み上げ用に表記をそろえる（半角カナを全角に、絵文字と「www」を除く）"""
    text = unicodedata.normalize("NFKC", text)
    text = EMOJI.sub("", text)
    text = LAUGH.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def usable(res):
    """読み上げる中身のあるレスか（絵文字・記号だけのレスや長すぎるレスは使わない）"""
    return res["no"] > 1 and len(res["text"]) <= MAX_RES_CHARS and len(CONTENT_CHAR.findall(res["text"])) >= MIN_RES_CHARS


def parent_of(res, by_no):
    """返信先のレス（同じスレの使えるレス）。なければNone"""
    for number in res.get("parents", []):
        parent = by_no.get((res["thread"], number))
        if parent is not None and parent["no"] < res["no"] and usable(parent):
            return parent
    return None


def needs_context(res):
    """「それな」「確かに」のような、元のレスがないと意味が通らない短いレスか"""
    return len(res["text"]) <= CONTEXT_MAX_CHARS and bool(CONTEXT_START.match(res["text"]))


def select_res(res_list):
    """返信の多いレスを優先し、動画の長さに収まるだけ選ぶ。返信のレスは返信先も一緒に選ぶ"""
    by_no = {(res["thread"], res["no"]): res for res in res_list}
    candidates = [res for res in res_list
                  if usable(res) and not (needs_context(res) and parent_of(res, by_no) is None)]
    candidates.sort(key=lambda res: (-res["replied"], res["thread"], res["no"]))

    selected = {}
    total = 0
    for res in candidates:
        group = [res]
        parent = parent_of(res, by_no)
        if parent is not None:
            group.insert(0, parent)
        new = [item for item in group if (item["thread"], item["no"]) not in selected]
        cost = sum(len(item["text"]) for item in new)
        if not new or total + cost > MAX_TOTAL_CHARS:
            continue
        for item in new:
            selected[(item["thread"], item["no"])] = item
        total += cost
    return sorted(selected.values(), key=lambda res: (res["thread"], res["no"]))


def res_keywords(res):
    return {word for word in KEYWORD.findall(res["text"]) if word not in STOP_WORDS}


def work_characters(theme):
    """テーマの作品のキャラ {表示名: [別名...]}（感想サイト/data.jsonのcharacters）。表示名は短い呼び名"""
    flat = compact(theme)
    for work in load_works():
        if any(compact(name) in flat for name in [work["title"]] + work.get("aliases", [])):
            return {(aliases[0] if aliases else name): [compact(alias) for alias in [name] + aliases]
                    for name, aliases in work.get("characters", {}).items()}
    return {}


def make_chains(res_list):
    """返信のやり取りを1つにまとめる。[[返信先, 返信, ...], ...]を投稿順で返す"""
    by_no = {(res["thread"], res["no"]): res for res in res_list}
    chains = {}
    for res in res_list:
        root = res
        while parent_of(root, by_no) is not None:
            root = parent_of(root, by_no)
        chains.setdefault((root["thread"], root["no"]), []).append(res)
    return [sorted(chain, key=lambda res: (res["thread"], res["no"])) for _, chain in sorted(chains.items())]


def group_by_topic(res_list, characters=None):
    """キャラ（別名をまとめる）と、多くのレスに出てくる語で話題を分ける。[(話題名, レス一覧)]をレス数の多い順で返す

    返信のやり取りは同じ話題にまとめ、返信先→返信の順に並べる
    """
    characters = characters or {}

    def main_character(chain):
        """やり取りの中で最も多く名前が出るキャラ（同数なら先に出たキャラ）"""
        text = compact(" ".join(res["text"] for res in chain))
        mentions = []
        for name, aliases in characters.items():
            positions = [match.start() for alias in aliases for match in re.finditer(re.escape(alias), text)]
            if positions:
                mentions.append((-len(positions), min(positions), name))
        return min(mentions)[2] if mentions else None

    # キャラが出てくるやり取りは主役のキャラごとにまとめる
    by_character = {}
    remaining = []
    for chain in make_chains(res_list):
        name = main_character(chain)
        (by_character.setdefault(name, []) if name else remaining).append(chain)
    topics = []
    for name, chains in sorted(by_character.items(), key=lambda item: -len(item[1])):
        if len(chains) >= MIN_TOPIC_RES and len(topics) < MAX_TOPICS:
            # 話題名は、そのスレで一番多く使われている呼び名（「テディ・ナイト」なら「テディ」）
            text = compact(" ".join(res["text"] for chain in chains for res in chain))
            topics.append((max(characters[name], key=lambda alias: (text.count(alias), -len(alias))), chains))
        else:
            remaining += chains
    # 残りは多く出てくる語で分ける
    remaining = [(chain, {word for res in chain for word in res_keywords(res)}) for chain in remaining]
    while remaining and len(topics) < MAX_TOPICS:
        counts = Counter(word for _, words in remaining for word in words)
        word, count = counts.most_common(1)[0] if counts else (None, 0)
        if count < MIN_TOPIC_RES:
            break
        topics.append((word, [chain for chain, words in remaining if word in words]))
        remaining = [(chain, words) for chain, words in remaining if word not in words]
    topics.sort(key=lambda topic: -len(topic[1]))
    if remaining:
        topics.append(("その他", sorted((chain for chain, _ in remaining), key=lambda chain: (chain[0]["thread"], chain[0]["no"]))))
    # 話題内はやり取りの投稿順
    return [(name, [res for chain in chains for res in chain]) for name, chains in topics]


# ---------------------------------------------------------------- コマ収集

def collect_panels(res_list, topics, panel_dir, threads, warnings):
    """各レスに関連する直近の画像を割り当てて保存し、{(スレ番号, レス番号): コマ画像の相対パス}を2つ返す

    1つ目は関連する画像すべて、2つ目は画像を貼ったレス自身か、その返信のレスだけ（コマ割り当てで最優先する）
    """
    image_res = [res for res in res_list if res["images"]]
    saved = {}
    panels = {}
    links = {}
    for _, items in topics:
        for res in items:
            before = [r for r in image_res if r["thread"] == res["thread"] and r["no"] <= res["no"]]
            for source_res in reversed(before):
                if not is_related(res, source_res):
                    continue
                source_key = (source_res["thread"], source_res["no"])
                if source_key not in saved:
                    saved[source_key] = store_panel(source_res, threads[source_res["thread"]], panel_dir, warnings)
                if saved[source_key]:
                    panels[(res["thread"], res["no"])] = saved[source_key]
                    if res["no"] == source_res["no"] or res["no"] in source_res["replies"]:
                        links[(res["thread"], res["no"])] = saved[source_key]
                    break
    save_thread_image_info(res_list, saved, panel_dir)
    return panels, links


def save_thread_image_info(res_list, saved, panel_dir):
    """スレの画像ごとに、画像を貼ったレスと、その返信の文章を保存する"""
    by_no = {(res["thread"], res["no"]): res for res in res_list}
    info = {}
    for (thread, number), panel in saved.items():
        if not panel:
            continue
        res = by_no[(thread, number)]
        replies = [by_no[(thread, reply)]["text"] for reply in res["replies"] if (thread, reply) in by_no]
        info[os.path.basename(panel)] = " ".join([res["text"]] + replies)
    if info:
        with open(os.path.join(panel_dir, THREAD_IMAGE_INFO_FILE), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)


def is_related(res, image_res):
    """画像のレス自身か、その返信か、本文のキーワードが共通するレスだけを関連ありとする"""
    return (res["no"] == image_res["no"] or res["no"] in image_res["replies"]
            or bool(res_keywords(res) & res_keywords(image_res)))


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
    warnings.append(f"レス{res['no']}の画像はコマの条件（白黒の漫画・サイズ）に合わないか、取得できませんでした")
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
    """コマとして使える画像なら拡張子を返す（小さいアイコンやカラー画像は除外）"""
    try:
        with Image.open(path) as image:
            if min(image.size) < MIN_PANEL_SIDE or not is_monochrome(image):
                return None
            return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(image.format)
    except OSError:
        return None


def color_ratio(image):
    """色の付いた画素の割合を返す"""
    red, green, blue = image.convert("RGB").resize((200, 200)).split()
    chroma = ImageChops.lighter(ImageChops.lighter(ImageChops.difference(red, green), ImageChops.difference(green, blue)),
                                ImageChops.difference(red, blue))
    histogram = chroma.histogram()
    return sum(histogram[PANEL_CHROMA:]) / sum(histogram)


def is_monochrome(image):
    """漫画本編は白黒なので、色の付いた画素が少ない画像だけを切り抜きとして扱う"""
    return color_ratio(image) <= MAX_PANEL_COLOR_RATIO


# ---------------------------------------------------------------- 出力

def make_rows(theme, topics, panels, links, title_voice, narration_voices):
    """[音声, セリフ, コマ画像, 話題, スレの画像]の行を作る。冒頭と締めは定型文

    5列目は、そのレス自身が貼った画像か、返信先のレスが貼った画像（コマ割り当てで最優先する）
    """
    first_panel = next((panels[key] for key in panels), "")
    rows = [
        [title_voice, f"今回は「{theme}」に対する読者の反応を紹介します！", first_panel, ""],
        [title_voice, "この動画はネタバレを含むため、ご注意ください。チャンネル登録、高評価よろしくお願いします！それではどうぞ！",
         first_panel, ""],
    ]
    count = 0
    last_panel = first_panel
    for topic, items in topics:
        for res in items:
            last_panel = panels.get((res["thread"], res["no"]), "")
            rows.append([narration_voices[count % len(narration_voices)], res["text"], last_panel, topic,
                         links.get((res["thread"], res["no"]), "")])
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
