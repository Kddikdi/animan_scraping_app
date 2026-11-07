"""集めた画像の手がかりを読み取る（画像を集めたときに実行し、結果を保存して使い回す）

    ・吹き出しの文字: 吹き出しらしい白い領域を切り出し、漫画用の文字読み取り（manga-ocr）にかけて 文字読み取り.json に保存する
    ・キャラの別名: 記事の『潔 世一』『ミヒャエル・カイザー』のような書き方から、姓・名を別名として拾う
"""
import json
import os
import re
import unicodedata

from fetch_images import IMAGE_EXTENSIONS, IMAGE_INFO_FILE

OCR_CACHE_FILE = "文字読み取り.json"
MAX_BUBBLES = 12

# 『姓 名』（姓と名の間に空白）と『名・姓』（カタカナの中黒）
SPACED_NAME = re.compile(r"『([一-龥々ァ-ヶー]{1,5})[ 　]([一-龥々ぁ-んァ-ヶー]{1,6})』")
DOTTED_NAME = re.compile(r"『([ァ-ヶー]{2,})・([ァ-ヶー]{2,})』")


def compact(text):
    """表記ゆれ（全角半角・大小文字・空白・記号）をそろえる"""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s『』「」【】〈〉“”\"'・、。,.!?！？…~〜()（）\-]", "", text)


def read_ocr(folder):
    """フォルダの画像の吹き出しの文字を読み取る。読み取った結果は 文字読み取り.json に保存し、次回から使い回す"""
    cache_path = os.path.join(folder, OCR_CACHE_FILE)
    cache = {}
    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    changed = False
    for root, _, names in os.walk(folder):
        for name in sorted(names):
            relpath = os.path.normpath(os.path.relpath(os.path.join(root, name), folder))
            if name.lower().endswith(IMAGE_EXTENSIONS) and relpath not in cache:
                cache[relpath] = read_bubbles(os.path.join(root, name))
                changed = True
    if changed:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    return cache


_ocr_model = None


def read_bubbles(path):
    """吹き出しらしい白い領域を切り出して、漫画用の文字読み取り（manga-ocr）にかける"""
    global _ocr_model
    import cv2
    import numpy as np
    from PIL import Image

    if _ocr_model is None:
        from manga_ocr import MangaOcr
        _ocr_model = MangaOcr()
    with Image.open(path) as image:
        image = image.convert("RGB")
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    _, white = cv2.threshold(gray, 230, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(white, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    texts = []
    boxes = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if not 0.004 * width * height <= area <= 0.25 * width * height:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if any(x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh for bx, by, bw, bh in boxes):
            continue
        crop = gray[y:y + h, x:x + w]
        # 吹き出しは白地に文字が少しある。真っ白や絵の部分は飛ばす
        dark = (crop < 100).mean()
        if not 0.02 <= dark <= 0.35 or area / (w * h) < 0.5:
            continue
        boxes.append((x, y, w, h))
        text = _ocr_model(image.crop((x, y, x + w, y + h)))
        if len(compact(text)) >= 2:
            texts.append(text)
        if len(boxes) >= MAX_BUBBLES:
            break
    return " / ".join(texts)


def find_names(text):
    """文章から {正式名: [別名...]} を拾う"""
    names = {}
    for family, given in SPACED_NAME.findall(text):
        names[family + given] = [family, given]
    for first, last in DOTTED_NAME.findall(text):
        names[f"{first}・{last}"] = [last, first]
    return names


def learn_characters(characters, folder):
    """フォルダの画像情報.jsonの文章からキャラの別名を拾って characters に足し、足した名前を返す

    糸師凛・糸師冴の「糸師」のように、ほかのキャラと重なる別名は足さない
    """
    info_path = os.path.join(folder, IMAGE_INFO_FILE)
    if not os.path.isfile(info_path):
        return []
    with open(info_path, encoding="utf-8") as f:
        text = " ".join(entry.get("context", "") for entry in json.load(f))
    found = find_names(text)
    shared = {alias for alias in sum(found.values(), []) if sum(alias in aliases for aliases in found.values()) > 1}
    added = []
    for name, aliases in found.items():
        taken = shared | {alias for other, others in characters.items() if other != name for alias in [other] + others}
        current = characters.setdefault(name, [])
        new = [alias for alias in aliases if alias not in current and alias not in taken and alias != name]
        if new:
            current += new
            added.append(f"{name}（{'・'.join(new)}）")
        elif not current:
            del characters[name]
    return added
