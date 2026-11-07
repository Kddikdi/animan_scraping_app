"""反応集の台本のコマ画像を、コメントの内容に合わせて選ぶ

    ・候補は、スレに貼られた画像（コマ画像/<台本名>/）と、感想サイトから集めた画像（参考画像/<作品>/<サイト>/<N>話 画像/）
    ・スレの画像は、住人が話題にしているコマそのものなので最優先する。台本作成で返信の関係から付いた画像（台本の5列目）は特に重く扱う
    ・画像ごとの手がかりは、画像の説明（目で見て書いたもの）、吹き出しの文字（OCR）、記事で画像の前後に書かれた文章、
      スレの画像なら貼ったレスと返信の文章。画像の説明を一番重く扱う
    ・指定の話の画像を優先する。過去の話の画像（「出典 43巻」等の振り返りを含む）は、コメントが過去の話に触れているか、非常に強く合うときだけ使う
    ・2セリフごとに、コメントのキャラ名・語句・セリフが手がかりと重なる画像を選ぶ。合う画像がなければ無理に切り替えず、前の画像を続ける
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
from .image_reading import compact, read_bubbles, read_ocr

LINES_PER_IMAGE = 2
# 台本の5列目: 台本作成で返信の関係から付いたスレの画像
THREAD_PANEL_COLUMN = 4
THREAD_IMAGE_INFO_FILE = "スレ画像情報.json"
THREAD_OCR_FILE = "スレ文字読み取り.json"
SITE_COPY_FOLDER = "感想サイト"
# スレの画像は手がかりがしっかり合ったとき（THREAD_BONUS_MIN以上）に足す。返信の関係で付いた画像はさらに大きく足す
THREAD_BONUS = 1.5
THREAD_BONUS_MIN = 2.0
REPLY_LINK_SCORE = 6.0
# 画像の説明（目で見て書いたもの）で一致した語は、ほかの手がかりより重く数える
DESCRIPTION_WEIGHT = 2.0
# 直近この枚数に使った画像は点数を下げる（前の画像を続けて使うときは下げない）
RECENT_SLOTS = 3
RECENT_PENALTY = 1.0
# 合う画像がなければ前の画像を続ける。続けるのはこの場面数まで（同じ画像が長く続くと単調になる）
MAX_HOLD_SLOTS = 3
# 合う画像がなく別の画像に切り替えるときは、この先この枚数分のコメントに合う画像を避ける
LOOKAHEAD_SLOTS = 3
# 過去の話の画像は点数を下げ、コメントが過去の話に触れていればPAST_MIN_SCORE、触れていなければSTRONG_PAST_SCORE以上で使う
PAST_CHAPTER_WEIGHT = 0.6
PAST_MIN_SCORE = 2.5
# 1つ前の話は同じ流れのことが多いので、過去の話として扱わず少し下げるだけにする
PREVIOUS_CHAPTER_WEIGHT = 0.85
STRONG_PAST_SCORE = 4.0
PAST_CUE = re.compile(r"前回|先週|前の話|昔|当時|かつて|以前|回想|初登場|\d+年前|\d+巻|(\d{1,4})話")
# この点数に満たなければ合う画像なし
MIN_SCORE = 0.5
# キャラ名・固有名詞（カタカナ3字以上）・セリフ・画像の説明のどれも一致しないときは、ありふれた語だけの一致として点数を下げる
WEAK_ONLY_FACTOR = 0.5
PROPER_NOUN = re.compile(r"[ァ-ヶー]{3,}|[a-z]{3,}")
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
              "反応", "紹介", "動画",
              # どの話・どの場面にも出てくる語は手がかりにならない
              "カッコ", "カッコいい", "シーン", "ラスト", "ファン", "デザイン", "レベル", "タイプ", "イメージ", "パワー",
              "ポイント", "メンバー", "スタイル", "気持", "過去", "意味", "面白", "登場", "年前", "人間", "解説", "最高",
              "感じ", "相手", "選手", "試合", "結局", "普通", "一番", "理解", "説明", "今後", "以外", "本気", "関係",
              "部分", "場面", "表現", "大事", "全然", "注目", "必要", "可能性", "状態", "存在", "世界", "攻撃", "勝負",
              "マジ", "カッケェ", "カッケー", "ヤバい", "ヤバ", "エモい", "最強", "神回", "激アツ", "感動", "好き"}


def script_path(csv_name):
    return csv_name if os.path.isfile(csv_name) else os.path.join(SCRIPT_DIR, csv_name)


def read_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return [row + [""] * (THREAD_PANEL_COLUMN + 1 - len(row)) for row in csv.reader(f)]


def main(csv_name, work_title, chapter, lines_per_image=LINES_PER_IMAGE):
    """台本のコマ画像列を、コメントに合う画像で書き換え、割り当ての一覧を返す"""
    csv_path = script_path(csv_name)
    rows = read_rows(csv_path)
    title = read_meta(csv_path)["title"]
    images = thread_images(title, chapter) + load_library(work_title, chapter)
    if not any(image["chapter"] == chapter for image in images):
        raise ValueError(f"{work_title} {chapter}話の画像がありません。先に感想サイト画像収集で集めてください")

    aliases = character_aliases(work_title)
    weigh_terms(images, aliases)
    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    report = []
    used = []
    held = 0
    slots = [(start, rows[start:start + lines_per_image]) for start in range(0, len(rows), lines_per_image)]
    texts = [(" ".join(row[1] for row in slot), " ".join(row[3] for row in slot)) for _, slot in slots]
    for index, (start, slot) in enumerate(slots):
        upcoming = texts[index + 1:index + 1 + LOOKAHEAD_SLOTS]
        links = {os.path.normpath(os.path.join(PANEL_DIR, row[THREAD_PANEL_COLUMN])) for row in slot if row[THREAD_PANEL_COLUMN]}
        image, reason = choose(*texts[index], images, used, chapter, aliases, upcoming, links, held)
        held = held + 1 if used and image is used[-1] else 0
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
    """指定の話までの感想サイトの画像を、手がかりつきで返す"""
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


def read_image_info(folder):
    info_path = os.path.join(folder, IMAGE_INFO_FILE)
    if not os.path.isfile(info_path):
        return {}
    with open(info_path, encoding="utf-8") as f:
        return {os.path.normpath(entry["file"]): entry for entry in json.load(f)}


def chapter_images(folder, chapter, site):
    info = read_image_info(folder)
    ocr = read_ocr(folder)
    images = []
    for root, _, names in os.walk(folder):
        for name in sorted(names):
            if not name.lower().endswith(IMAGE_EXTENSIONS):
                continue
            path = os.path.join(root, name)
            relpath = os.path.normpath(os.path.relpath(path, folder))
            entry = info.get(relpath, {})
            images.append({"path": path, "folder": folder, "file": relpath, "chapter": source_chapter(relpath, chapter),
                           "site": site, "order": len(images), "thread": False,
                           "context": " ".join(filter(None, [entry.get("context", ""), entry.get("source") or ""])),
                           "description": entry.get("description", ""), "ocr": ocr.get(relpath, "")})
    return images


def thread_images(title, chapter):
    """台本作成でスレから集めた画像を、貼ったレスと返信の文章つきで返す"""
    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    if not os.path.isdir(panel_dir):
        return []
    info = {}
    if os.path.isfile(os.path.join(panel_dir, THREAD_IMAGE_INFO_FILE)):
        with open(os.path.join(panel_dir, THREAD_IMAGE_INFO_FILE), encoding="utf-8") as f:
            info = json.load(f)
    ocr_path = os.path.join(panel_dir, THREAD_OCR_FILE)
    ocr = {}
    if os.path.isfile(ocr_path):
        with open(ocr_path, encoding="utf-8") as f:
            ocr = json.load(f)
    names = sorted(name for name in os.listdir(panel_dir)
                   if name.lower().endswith(IMAGE_EXTENSIONS) and os.path.isfile(os.path.join(panel_dir, name)))
    missing = [name for name in names if name not in ocr]
    for name in missing:
        ocr[name] = read_bubbles(os.path.join(panel_dir, name))
    if missing:
        with open(ocr_path, "w", encoding="utf-8") as f:
            json.dump(ocr, f, ensure_ascii=False, indent=2)
    return [{"path": os.path.join(panel_dir, name), "folder": panel_dir, "file": name, "chapter": chapter, "site": "スレ",
             "order": order, "thread": True, "context": info.get(name, ""), "description": "", "ocr": ocr.get(name, "")}
            for order, name in enumerate(names)]


def source_chapter(relpath, chapter):
    """画像の話数。「出典 1107話」「出典 43巻」のサブフォルダの画像は、出典の話数（巻なら0=古い話）とする"""
    folder = relpath.split(os.sep)[0]
    if folder.startswith("出典 ") and folder != relpath:
        matched = re.fullmatch(r"出典 (\d+)話", folder)
        if matched:
            return int(matched.group(1))
        if re.fullmatch(r"出典 \d+巻", folder):
            return 0
    return chapter


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
    # キャラの別名そのもの（「ゾロ」）は正式名（「ロロノア・ゾロ」）として数えたので、語句としては数えない
    found_aliases = {alias for name in terms for alias in aliases[name]}
    words = {compact(word) for word in KEYWORD.findall(unicodedata.normalize("NFKC", text))}
    terms |= {word for word in words if not any(alias in word for alias in found_aliases)} - {compact(w) for w in STOP_WORDS}
    return terms - _work_words


def weigh_terms(images, aliases):
    """画像ごとの語句と、語句の珍しさ（多くの画像に出てくる語ほど軽い）を計算する"""
    for image in images:
        image["description_terms"] = terms_in(image["description"], aliases)
        image["terms"] = terms_in(" ".join([image["context"], image["ocr"], image["description"]]), aliases)
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
        described = shared & image["description_terms"]
        value = sum(image["weights"][term] * (DESCRIPTION_WEIGHT if term in described else 1) for term in shared)
        if not reasons and not described and not any(term in aliases or PROPER_NOUN.fullmatch(term) for term in shared):
            value *= WEAK_ONLY_FACTOR
        total += value
        reasons.append(("説明:" if described else "語句:") + "・".join(sorted(shared)[:4]))
    return total, " ".join(reasons)


def mentions_past(text, chapter):
    """コメントが過去の話に触れているか（「前回」「昔」「1150話」など。指定の話数そのものは除く）"""
    return any(not match.group(1) or int(match.group(1)) != chapter for match in PAST_CUE.finditer(unicodedata.normalize("NFKC", text)))


def choose(text, topic, images, used, chapter, aliases, upcoming=(), links=(), held=0):
    previous = used[-1] if used else None
    recent = {id(image) for image in used[-RECENT_SLOTS:]}
    counts = {}
    for image in used:
        counts[id(image)] = counts.get(id(image), 0) + 1
    past_minimum = PAST_MIN_SCORE if mentions_past(text, chapter) else STRONG_PAST_SCORE
    best, best_score, best_reason = None, 0.0, ""
    for image in images:
        value, reason = score(text, topic, image, aliases)
        if image["thread"] and os.path.normpath(image["path"]) in links:
            value += REPLY_LINK_SCORE
            reason = ("スレで返信の関係 " + reason).strip()
        elif image["thread"] and value >= THREAD_BONUS_MIN:
            value += THREAD_BONUS
            reason = "スレの画像 " + reason
        if image["chapter"] == chapter - 1:
            value *= PREVIOUS_CHAPTER_WEIGHT
        elif image["chapter"] != chapter:
            value *= PAST_CHAPTER_WEIGHT
            if value < past_minimum:
                continue
        if image is not previous:
            value -= 0.5 * counts.get(id(image), 0) + (RECENT_PENALTY if id(image) in recent else 0)
        if value > best_score:
            best, best_score, best_reason = image, value, reason
    if best is not None and best_score >= MIN_SCORE:
        past = "" if best["chapter"] == chapter else f"（{best['chapter'] or '過去の巻'}{'話' if best['chapter'] else ''}の画像）"
        return best, f"一致 {best_score:.1f} {best_reason}{past}"
    # 合う画像がなければ無理に切り替えず、前の画像を続ける
    if previous is not None and held < MAX_HOLD_SLOTS:
        return previous, "前の画像を続ける（合う画像なし）"
    # 続けすぎたときは、指定の話の画像を、この先のコメントに合いそうなもの・使った回数の多いものを避けて使う
    current = [image for image in images if image["chapter"] == chapter and image is not previous] or \
              [image for image in images if image["chapter"] == chapter]
    fallback = min(current, key=lambda image: (sum(score(*later, image, aliases)[0] for later in upcoming),
                                               counts.get(id(image), 0), id(image) in recent, image["site"], image["order"]))
    return fallback, "順番（合う画像なし）"


def copy_panel(image, panel_dir, title):
    """台本に書く相対パスを返す。感想サイトの画像はコマ画像フォルダにコピーする"""
    if image["thread"]:
        return os.path.relpath(image["path"], PANEL_DIR).replace("\\", "/")
    folder = os.path.join(panel_dir, SITE_COPY_FOLDER)
    os.makedirs(folder, exist_ok=True)
    relpath = os.path.relpath(image["path"], REFERENCE_IMAGE_DIR)
    name = safe_file_name(relpath.replace(os.sep, "_"))
    dest = os.path.join(folder, name)
    if not os.path.isfile(dest):
        shutil.copyfile(image["path"], dest)
    return f"{safe_file_name(title)}/{SITE_COPY_FOLDER}/{name}"


# ---------------------------------------------------------------- 目で見て直す

SHEET_COLUMNS = 5
SHEET_CELL = (300, 300)
SHEET_ROWS = 6


def candidate_images(csv_name, work_title, chapter):
    """見直し用の候補: スレの画像と、指定の話とその1つ前の話の感想サイトの画像（出典が過去の巻・話の振り返り画像は除く）"""
    title = read_meta(script_path(csv_name))["title"]
    library = [image for image in load_library(work_title, chapter) if chapter - 1 <= image["chapter"] <= chapter]
    return thread_images(title, chapter) + sorted(library, key=lambda image: (-image["chapter"], image["site"], image["path"]))


def contact_sheets(csv_name, work_title, chapter, out_dir):
    """候補の画像に番号を振った一覧画像と、番号ごとの手がかりの一覧（コマ候補.txt）を作り、パスを返す

    画像の説明がまだない画像は、番号の横に「未」と出る
    """
    from PIL import Image, ImageDraw, ImageFont

    csv_path = script_path(csv_name)
    images = candidate_images(csv_name, work_title, chapter)
    os.makedirs(out_dir, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 22)
    per_sheet = SHEET_COLUMNS * SHEET_ROWS
    sheets = []
    for start in range(0, len(images), per_sheet):
        page = images[start:start + per_sheet]
        rows = (len(page) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
        sheet = Image.new("RGB", (SHEET_CELL[0] * SHEET_COLUMNS, SHEET_CELL[1] * rows), "white")
        draw = ImageDraw.Draw(sheet)
        for offset, image in enumerate(page):
            x, y = (offset % SHEET_COLUMNS) * SHEET_CELL[0], (offset // SHEET_COLUMNS) * SHEET_CELL[1]
            with Image.open(image["path"]) as picture:
                thumb = picture.convert("RGB")
                thumb.thumbnail((SHEET_CELL[0] - 10, SHEET_CELL[1] - 40))
            sheet.paste(thumb, (x + 5, y + 35))
            label = "スレ" if image["thread"] else f"{image['chapter']}話"
            mark = "" if image["description"] or image["thread"] else " 未"
            draw.text((x + 5, y + 3), f"{start + offset + 1}  {label}{mark}", fill="red", font=font)
        path = os.path.join(out_dir, f"コマ候補_{start // per_sheet + 1}.jpg")
        sheet.save(path, quality=85)
        sheets.append(path)
    lines = []
    for number, image in enumerate(images, 1):
        source = "スレ" if image["thread"] else os.path.relpath(image["path"], REFERENCE_IMAGE_DIR)
        lines.append(f"{number}  {source}\n    説明: {image['description'] or '（なし）'}\n    文章: {image['context'][:120]}\n"
                     f"    吹き出し: {image['ocr'][:80]}")
    list_path = os.path.splitext(csv_path)[0] + ".コマ候補.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return sheets, list_path


def describe_images(csv_name, work_title, chapter, descriptions):
    """{候補番号: 説明} を、感想サイトの画像の 画像情報.json に書き込む（スレの画像は貼ったレスの文章があるので対象外）

    説明は「誰が・どんな場面か・セリフ」を短く書く。蓄積され、同じ画像を使う次回以降の動画でも使われる
    """
    images = candidate_images(csv_name, work_title, chapter)
    written = []
    for number, description in descriptions.items():
        image = images[number - 1]
        if image["thread"]:
            continue
        info_path = os.path.join(image["folder"], IMAGE_INFO_FILE)
        entries = []
        if os.path.isfile(info_path):
            with open(info_path, encoding="utf-8") as f:
                entries = json.load(f)
        entry = next((entry for entry in entries if os.path.normpath(entry["file"]) == image["file"]), None)
        if entry is None:
            entry = {"file": image["file"]}
            entries.append(entry)
        entry["description"] = description
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        written.append(f"{number}  {description}")
    return written


def fix_panels(csv_name, work_title, chapter, fixes):
    """{(開始行, 終了行): 候補番号} の指定どおりに台本のコマ画像を差し替え、割り当ての一覧にも書き足す"""
    csv_path = script_path(csv_name)
    images = candidate_images(csv_name, work_title, chapter)
    title = read_meta(csv_path)["title"]
    panel_dir = os.path.join(PANEL_DIR, safe_file_name(title))
    rows = read_rows(csv_path)
    notes = []
    for (first, last), number in fixes.items():
        panel = copy_panel(images[number - 1], panel_dir, title)
        for row in rows[first - 1:last]:
            row[2] = panel
        notes.append(f"{first}-{last}行目  {os.path.basename(panel)}  目で見て差し替え（候補{number}）  「{rows[first - 1][1][:30]}」")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    with open(os.path.splitext(csv_path)[0] + ".コマ割り当て.txt", "a", encoding="utf-8") as f:
        f.write("\n".join(["", "■ 目で見て差し替えた場面"] + notes) + "\n")
    return notes
