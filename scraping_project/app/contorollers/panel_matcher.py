"""反応集の台本のコマ画像を、コメントの内容に合わせて選ぶ

    ・候補は感想サイトから集めた画像（参考画像/<作品>/<サイト>/<N>話 画像/）。指定の話を優先し、過去の話は強く合うときだけ使う
    ・画像ごとの手がかりは、記事で画像の前後に書かれた文章（画像情報.json）と、吹き出しの文字（OCR、文字読み取り.json）。
      どちらも画像を集めたときに作られる（image_reading）。作られていない画像はここで読み取る
    ・2セリフごとに1枚、コメントのキャラ名・語句・セリフが手がかりと重なる画像を選ぶ。重ならなければ話の画像を順に使う
    ・どの画像を何の理由で選んだかは 台本ファイル/<台本名>.コマ割り当て.txt に書き出す
"""
import csv
import json
import math
import os
import re
import shutil
import unicodedata
from difflib import SequenceMatcher

from fetch_images import IMAGE_EXTENSIONS, IMAGE_INFO_FILE
from .create_animan_video import PANEL_DIR, SCRIPT_DIR, read_meta, safe_file_name
from .find_review_sites import REFERENCE_IMAGE_DIR, load_works
from .image_reading import compact, read_ocr

LINES_PER_IMAGE = 2
# 直前と同じ画像は使わない。直近この枚数に使った画像は点数を下げる
RECENT_SLOTS = 3
RECENT_PENALTY = 1.0
# 合う画像がなく順番に使うときは、この先この枚数分のコメントに合う画像を避ける
LOOKAHEAD_SLOTS = 3
# 過去の話の画像は点数を下げる。この点数に満たなければ合う画像なしとして順番に使う
PAST_CHAPTER_WEIGHT = 0.6
MIN_SCORE = 0.5
# コメントと吹き出しの文字がこの字数以上続けて一致すれば、セリフの引用とみなす。
# 「それでは」「と思ってる」のようなひらがなだけの言い回しは、どこにでも出てくるので数えない
MIN_QUOTE_CHARS = 5
# これ以上の長さで一致すれば、ひらがなが多くても（「殺すしかないな」）セリフとみなす
LONG_QUOTE_CHARS = 7
HIRAGANA_ONLY = re.compile(r"[ぁ-ゖっゃゅょー]+")
QUOTE_SCORE = 5.0

KEYWORD = re.compile(r"[ァ-ヶー]{2,}|[一-龥]{2,}|[A-Za-z]{3,}")
STOP_WORDS = {"感想", "今回", "今週", "来週", "最新", "最新話", "最後", "最初", "読者", "漫画", "作者", "展開", "本当",
              "自分", "個人的", "ネタバレ", "出典", "引用", "スポンサーリンク", "ページ", "キャラ", "記事", "前回", "次回",
              "反応", "紹介", "動画"}


def main(csv_name, work_title, chapter, lines_per_image=LINES_PER_IMAGE):
    """台本のコマ画像列を、コメントに合う感想サイトの画像で書き換え、割り当ての一覧を返す"""
    csv_path = csv_name if os.path.isfile(csv_name) else os.path.join(SCRIPT_DIR, csv_name)
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = [row + [""] * (4 - len(row)) for row in csv.reader(f)]
    images = load_library(work_title, chapter)
    if not any(image["chapter"] == chapter for image in images):
        raise ValueError(f"{work_title} {chapter}話の画像がありません。先に感想サイト画像収集で集めてください")

    aliases = character_aliases(work_title)
    weigh_terms(images, aliases)
    title = read_meta(csv_path)["title"]
    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    report = []
    used = []
    slots = [(start, rows[start:start + lines_per_image]) for start in range(0, len(rows), lines_per_image)]
    texts = [(" ".join(row[1] for row in slot), " ".join(row[3] for row in slot)) for _, slot in slots]
    for index, (start, slot) in enumerate(slots):
        upcoming = texts[index + 1:index + 1 + LOOKAHEAD_SLOTS]
        image, reason = choose(*texts[index], images, used, chapter, aliases, upcoming)
        used.append(image)
        panel = copy_panel(image, panel_dir, title)
        for row in slot:
            row[2] = panel
        report.append(f"{start + 1}-{start + len(slot)}行目  {os.path.basename(panel)}  {reason}  「{slot[0][1][:30]}」")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    report_path = os.path.splitext(csv_path)[0] + ".コマ割り当て.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print(f"コマを割り当てました: {len(report)}か所、画像{len(set(map(id, used)))}枚（{report_path}）")
    return report


# ---------------------------------------------------------------- 画像と手がかり

def load_library(work_title, chapter):
    """指定の話までの集めた画像を、手がかりの文章つきで返す"""
    work_dir = os.path.join(REFERENCE_IMAGE_DIR, safe_file_name(work_title))
    images = []
    if not os.path.isdir(work_dir):
        return images
    for site in sorted(os.listdir(work_dir)):
        site_dir = os.path.join(work_dir, site)
        for chapter_name in sorted(os.listdir(site_dir)) if os.path.isdir(site_dir) else []:
            matched = re.fullmatch(r"(\d+)話 画像", chapter_name)
            if not matched or int(matched.group(1)) > chapter:
                continue
            images += chapter_images(os.path.join(site_dir, chapter_name), int(matched.group(1)), site)
    return images


def chapter_images(folder, chapter, site):
    info = {}
    info_path = os.path.join(folder, IMAGE_INFO_FILE)
    if os.path.isfile(info_path):
        with open(info_path, encoding="utf-8") as f:
            info = {os.path.normpath(entry["file"]): entry for entry in json.load(f)}
    ocr = read_ocr(folder)
    images = []
    for root, _, names in os.walk(folder):
        for name in sorted(names):
            if not name.lower().endswith(IMAGE_EXTENSIONS):
                continue
            path = os.path.join(root, name)
            relpath = os.path.normpath(os.path.relpath(path, folder))
            entry = info.get(relpath, {})
            images.append({"path": path, "chapter": chapter, "site": site, "order": len(images),
                           "context": " ".join(filter(None, [entry.get("context", ""), entry.get("source") or ""])),
                           "ocr": ocr.get(relpath, "")})
    return images


# ---------------------------------------------------------------- 選び方

_work_words = set()


def character_aliases(work_title):
    """キャラの別名 {正式名: [別名...]}（data.jsonのcharacters）。作品名とその別名は、どの画像にも合うので手がかりにしない"""
    work = next((work for work in load_works() if work["title"] == work_title), {})
    _work_words.clear()
    _work_words.update(compact(name) for name in [work_title] + work.get("aliases", []))
    return {name: [compact(alias) for alias in [name] + names] for name, names in work.get("characters", {}).items()}


def all_aliases(aliases):
    return {alias for names in aliases.values() for alias in names}


def terms_in(text, aliases):
    """文章に出てくるキャラ（正式名）と語句"""
    flat = compact(text)
    terms = {name for name, names in aliases.items() if any(alias in flat for alias in names)}
    terms |= {compact(word) for word in KEYWORD.findall(unicodedata.normalize("NFKC", text))} - {compact(w) for w in STOP_WORDS}
    return terms - _work_words


def weigh_terms(images, aliases):
    """画像ごとの語句と、語句の珍しさ（多くの画像に出てくる語ほど軽い）を計算する"""
    for image in images:
        image["terms"] = terms_in(image["context"] + " " + image["ocr"], aliases)
        image["ocr_flat"] = [compact(part) for part in image["ocr"].split(" / ") if part]
    count = len(images)
    df = {}
    for image in images:
        for term in image["terms"]:
            df[term] = df.get(term, 0) + 1
    for image in images:
        image["weights"] = {term: math.log(1 + count / df[term]) for term in image["terms"]}


def score(text, topic, image, aliases):
    """コメントと画像の合い具合と、その理由を返す"""
    reasons = []
    total = 0.0
    flat = compact(text)
    for bubble in image["ocr_flat"]:
        match = SequenceMatcher(None, flat, bubble).find_longest_match(0, len(flat), 0, len(bubble))
        quote = bubble[match.b:match.b + match.size]
        # ひらがなを除いた部分が短いもの（「と思ってる」）と、キャラ名等の語句に助詞が付いただけのもの（「フォックスに」）は
        # セリフではなく語句として数える
        core = HIRAGANA_ONLY.sub("", quote)
        long_quote = match.size >= LONG_QUOTE_CHARS
        if match.size >= MIN_QUOTE_CHARS and (long_quote or len(core) >= 3) and core not in all_aliases(aliases) | terms_in(text, aliases):
            total += QUOTE_SCORE + match.size / 4
            reasons.append(f"セリフ「{quote}」")
            break
    shared = terms_in(text + " " + topic, aliases) & image["terms"]
    if shared:
        total += sum(image["weights"][term] for term in shared)
        reasons.append("語句:" + "・".join(sorted(shared)[:4]))
    return total, " ".join(reasons)


def choose(text, topic, images, used, chapter, aliases, upcoming=()):
    previous = id(used[-1]) if used else None
    recent = {id(image) for image in used[-RECENT_SLOTS:]}
    counts = {}
    for image in used:
        counts[id(image)] = counts.get(id(image), 0) + 1
    best, best_score, best_reason = None, 0.0, ""
    for image in images:
        if id(image) == previous:
            continue
        value, reason = score(text, topic, image, aliases)
        value *= 1 if image["chapter"] == chapter else PAST_CHAPTER_WEIGHT
        value -= 0.5 * counts.get(id(image), 0) + (RECENT_PENALTY if id(image) in recent else 0)
        if value > best_score:
            best, best_score, best_reason = image, value, reason
    if best is not None and best_score >= MIN_SCORE:
        past = "" if best["chapter"] == chapter else f"（{best['chapter']}話の画像）"
        return best, f"一致 {best_score:.1f} {best_reason}{past}"
    # 合う画像がなければ、指定の話の画像を、この先のコメントに合いそうなもの・使った回数の多いものを避けて使う
    current = [image for image in images if image["chapter"] == chapter and id(image) != previous] or \
              [image for image in images if image["chapter"] == chapter]
    fallback = min(current, key=lambda image: (sum(score(*later, image, aliases)[0] for later in upcoming),
                                               counts.get(id(image), 0), id(image) in recent, image["site"], image["order"]))
    return fallback, "順番（合う画像なし）"


def copy_panel(image, panel_dir, title):
    """コマ画像フォルダにコピーし、台本に書く相対パスを返す"""
    folder = os.path.join(panel_dir, "感想サイト")
    os.makedirs(folder, exist_ok=True)
    relpath = os.path.relpath(image["path"], REFERENCE_IMAGE_DIR)
    name = safe_file_name(relpath.replace(os.sep, "_"))
    dest = os.path.join(folder, name)
    if not os.path.isfile(dest):
        shutil.copyfile(image["path"], dest)
    return f"{safe_file_name(title)}/感想サイト/{name}"
