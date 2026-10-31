"""反応集サムネイルのテンプレート

中央にコマ、四隅に色違いの吹き出し、下部に極太のグラデーション文字を置く定番の構成
見た目を増やすときはTEMPLATESに追加する（配置は共通）
"""
import colorsys
import math
import os
import random
import re

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1920, 1080

# 吹き出し（左上, 右上, 左下, 右下）。右側は尻尾を左右反転する
BUBBLE_WIDTH, BUBBLE_HEIGHT = 594, 352
BUBBLE_STROKE = 28
BUBBLE_MARGIN = 18
BUBBLE_POSITIONS = [
    (BUBBLE_MARGIN, 18, False),
    (WIDTH - BUBBLE_MARGIN - BUBBLE_WIDTH, 18, True),
    (BUBBLE_MARGIN, 400, False),
    (WIDTH - BUBBLE_MARGIN - BUBBLE_WIDTH, 400, True),
]
BUBBLE_MAX_FONT_SIZE = 100
BUBBLE_MIN_FONT_SIZE = 56
BUBBLE_TEXT_WIDTH = 520
BUBBLE_TEXT_HEIGHT = 270
BUBBLE_LINE_CHARS = 8

BOTTOM_CENTER_Y = 945
BOTTOM_MAX_FONT_SIZE = 230
BOTTOM_MIN_FONT_SIZE = 80

# コマの基準サイズ（倍率1のとき。この枠に収まる最大の大きさ）
PANEL_MAX_WIDTH = 1300
PANEL_MAX_HEIGHT = 820
PANEL_CENTER_Y = 420

# 集中線の中心
LINES_CENTER = (WIDTH // 2, 430)
LINES_COUNT = 190

# 文節の切れ目になりやすい文字（吹き出しの改行位置）
BREAK_AFTER = "はがをにでとものへや、。！？!?…"

FONT_CANDIDATES = [
    # Windows（極太）
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
    "C:/Windows/Fonts/HGRSGU.TTC",
    "C:/Windows/Fonts/meiryob.ttc",
    # Mac
    "/System/Library/Fonts/ヒラギノ角ゴシック W9.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]

TEMPLATES = {
    "red": {
        "label": "赤黒文字（集中線）",
        "background": "lines",
        "bubble_colors": ["#ff00d0", "#16a800", "#0030ff", "#ff8000"],
        "bottom_colors": ["#000000", "#ff0000"],
        "bottom_outline": "#ff00ff",
    },
    "navy": {
        "label": "紺赤文字（集中線）",
        "background": "lines",
        "bubble_colors": ["#ff2fb0", "#ff7a00", "#1e78ff", "#16b000"],
        "bottom_colors": ["#0a1a80", "#e0003c"],
        "bottom_outline": "#ff30c0",
    },
    "rainbow": {
        "label": "赤黒文字（虹背景）",
        "background": "rainbow",
        "bubble_colors": ["#ff0000", "#27c705", "#0000ff", "#ff8000"],
        "bottom_colors": ["#000000", "#ff0000"],
        "bottom_outline": "#ff00ff",
    },
}
DEFAULT_TEMPLATE = "red"


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError("日本語フォントが見つかりません。FONT_CANDIDATESにフォントのパスを追加してください")


def load_font(font_path, size):
    font = ImageFont.truetype(font_path, size)
    # 可変フォントは最も太いウェイトにする
    if font_path.endswith("-VF.ttf"):
        font.set_variation_by_axes([900])
    return font


def default_settings(rows, meta):
    """台本からサムネイルの初期値を作る。rowsは(音声, セリフ, コマ画像, 話題)の一覧"""
    bubbles = pick_bubble_texts(rows)
    panel = next((row[2] for row in rows if row[2]), "")
    # テーマ未保存の台本はタイトルの【】内を使う
    theme = re.search(r"【(.+?)】", meta["title"])
    return {
        "template": DEFAULT_TEMPLATE,
        "bubbles": bubbles,
        "bottom": meta.get("theme") or (theme.group(1) if theme else meta["title"]),
        "image": panel,
        "image_scale": 1.0,
        "watermark": "",
    }


def pick_bubble_texts(rows):
    """短いレスを話題が偏らないように4つ選ぶ"""
    candidates = [(row[1], row[3]) for row in rows if row[3] and 4 <= len(row[1]) <= 16]
    picked = []
    used_topics = set()
    for text, topic in candidates:
        if topic not in used_topics and len(picked) < 4:
            picked.append(text)
            used_topics.add(topic)
    for text, _ in candidates:
        if text not in picked and len(picked) < 4:
            picked.append(text)
    return picked + [""] * (4 - len(picked))


def render_thumbnail(settings, panel_path=None):
    template = TEMPLATES.get(settings["template"], TEMPLATES[DEFAULT_TEMPLATE])
    font_path = find_font()

    image = make_background(template["background"])
    if panel_path:
        draw_panel(image, panel_path, settings["image_scale"])
    for (x, y, flipped), color, text in zip(BUBBLE_POSITIONS, template["bubble_colors"], settings["bubbles"]):
        if text.strip():
            draw_bubble(image, x, y, flipped, color)
            draw_bubble_text(image, text, x, y, color, font_path)
    if settings["bottom"].strip():
        draw_bottom_text(image, settings["bottom"].strip(), template, font_path)
    if settings["watermark"].strip():
        draw_watermark(image, settings["watermark"].strip(), font_path)
    return image


# ---------------------------------------------------------------- 背景

def make_background(kind):
    if kind == "rainbow":
        # 虹の上に白い光の線
        row = Image.new("RGB", (WIDTH, 1))
        for x in range(WIDTH):
            r, g, b = colorsys.hsv_to_rgb(x / WIDTH * 0.85, 1, 1)
            row.putpixel((x, 0), (int(r * 255), int(g * 255), int(b * 255)))
        background = row.resize((WIDTH, HEIGHT))
        return Image.composite(Image.new("RGB", (WIDTH, HEIGHT), "white"), background,
                               make_lines_mask().point(lambda v: v * 0.7))
    return Image.composite(Image.new("RGB", (WIDTH, HEIGHT), "black"), Image.new("RGB", (WIDTH, HEIGHT), "white"),
                           make_lines_mask())


def make_lines_mask():
    """中心に向かって細くなる集中線。毎回同じ形になるよう乱数を固定する"""
    rng = random.Random(1)
    scale = 2
    mask = Image.new("L", (WIDTH * scale, HEIGHT * scale), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = LINES_CENTER
    for index in range(LINES_COUNT):
        angle = (index + rng.random()) / LINES_COUNT * math.tau
        inner = rng.uniform(420, 640)
        outer = 1300
        half_width = rng.uniform(0.002, 0.012)
        points = [
            (cx + inner * math.cos(angle), cy + inner * math.sin(angle) * 0.75),
            (cx + outer * math.cos(angle - half_width), cy + outer * math.sin(angle - half_width)),
            (cx + outer * math.cos(angle + half_width), cy + outer * math.sin(angle + half_width)),
        ]
        draw.polygon([(x * scale, y * scale) for x, y in points], fill=255)
    return mask.resize((WIDTH, HEIGHT), Image.LANCZOS)


def draw_panel(image, panel_path, panel_scale):
    panel = Image.open(panel_path).convert("RGB")
    scale = min(PANEL_MAX_WIDTH / panel.width, PANEL_MAX_HEIGHT / panel.height) * panel_scale
    panel = panel.resize((int(panel.width * scale), int(panel.height * scale)), Image.LANCZOS)
    image.paste(panel, ((WIDTH - panel.width) // 2, PANEL_CENTER_Y - panel.height // 2))


# ---------------------------------------------------------------- 吹き出し

def draw_bubble(image, x, y, flipped, color):
    scale = 2
    width = (BUBBLE_WIDTH + BUBBLE_STROKE) * scale
    height = (BUBBLE_HEIGHT + BUBBLE_STROKE) * scale
    outer = Image.new("L", (width, height), 0)
    inner = Image.new("L", (width, height), 0)
    stroke = BUBBLE_STROKE // 2 * scale

    def shape(mask, grow):
        draw = ImageDraw.Draw(mask)
        left, top = stroke - grow, stroke - grow
        right = stroke + BUBBLE_WIDTH * scale + grow
        bottom = stroke + (BUBBLE_HEIGHT - 8) * scale + grow
        draw.ellipse([left, top, right, bottom], fill=255)
        # 尻尾（左下向き）
        tail = [(90, 280), (70, 350), (190, 325)]
        draw.polygon([(stroke + px * scale, stroke + py * scale) for px, py in tail], fill=255,
                     outline=255, width=grow * 2 + 1)

    shape(outer, stroke)
    shape(inner, 0)
    if flipped:
        outer = outer.transpose(Image.FLIP_LEFT_RIGHT)
        inner = inner.transpose(Image.FLIP_LEFT_RIGHT)
    size = (BUBBLE_WIDTH + BUBBLE_STROKE, BUBBLE_HEIGHT + BUBBLE_STROKE)
    position = (x - BUBBLE_STROKE // 2, y - BUBBLE_STROKE // 2)
    image.paste(Image.new("RGB", size, color), position, outer.resize(size, Image.LANCZOS))
    image.paste(Image.new("RGB", size, "white"), position, inner.resize(size, Image.LANCZOS))


def char_type(char):
    if "ァ" <= char <= "ヶ" or char == "ー":
        return "katakana"
    if "ぁ" <= char <= "ゖ":
        return "hiragana"
    if "一" <= char <= "龥":
        return "kanji"
    return "other"


def split_lines(text):
    """改行が無ければ文節の切れ目を探して行を揃える"""
    if "\n" in text:
        return [line.strip() for line in text.split("\n") if line.strip()]
    if len(text) <= BUBBLE_LINE_CHARS:
        return [text]
    count = math.ceil(len(text) / BUBBLE_LINE_CHARS)
    lines = []
    rest = text
    while count > 1:
        target = round(len(rest) / count)
        near = sorted(range(max(1, target - 3), min(len(rest), target + 4)), key=lambda i: abs(i - target))
        cut = next((i for i in near if rest[i - 1] in BREAK_AFTER and char_type(rest[i]) != "katakana"),
                   next((i for i in near if char_type(rest[i - 1]) != char_type(rest[i])), target))
        lines.append(rest[:cut])
        rest = rest[cut:]
        count -= 1
    return lines + [rest]


def draw_bubble_text(image, text, x, y, color, font_path):
    draw = ImageDraw.Draw(image)
    lines = split_lines(text.strip())
    for size in range(BUBBLE_MAX_FONT_SIZE, BUBBLE_MIN_FONT_SIZE - 1, -4):
        font = load_font(font_path, size)
        widest = max(draw.textlength(line, font=font) for line in lines)
        if widest <= BUBBLE_TEXT_WIDTH and size * 1.08 * len(lines) <= BUBBLE_TEXT_HEIGHT:
            break
    line_height = int(size * 1.08)
    center_x = x + BUBBLE_WIDTH // 2
    start_y = y + (BUBBLE_HEIGHT - 8) // 2 - line_height * (len(lines) - 1) // 2
    for index, line in enumerate(lines):
        position = (center_x, start_y + index * line_height)
        draw.text(position, line, font=font, fill=color, anchor="mm", stroke_width=18, stroke_fill=color)
        draw.text(position, line, font=font, fill="white", anchor="mm", stroke_width=10, stroke_fill="white")
    for index, line in enumerate(lines):
        draw.text((center_x, start_y + index * line_height), line, font=font, fill=color, anchor="mm")


# ---------------------------------------------------------------- 下部の文字

def draw_bottom_text(image, text, template, font_path):
    draw = ImageDraw.Draw(image)
    outer_stroke = 32
    for size in range(BOTTOM_MAX_FONT_SIZE, BOTTOM_MIN_FONT_SIZE - 1, -4):
        font = load_font(font_path, size)
        if draw.textlength(text, font=font) + outer_stroke * 2 <= WIDTH - 24:
            break
    position = (WIDTH // 2, BOTTOM_CENTER_Y)
    draw.text(position, text, font=font, fill=template["bottom_outline"], anchor="mm",
              stroke_width=outer_stroke, stroke_fill=template["bottom_outline"])
    draw.text(position, text, font=font, fill="white", anchor="mm", stroke_width=16, stroke_fill="white")

    # 文字の形にグラデーションを流し込む
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).text(position, text, font=font, fill=255, anchor="mm")
    top = BOTTOM_CENTER_Y - size // 2
    gradient = Image.linear_gradient("L").resize((WIDTH, size))
    top_color, bottom_color = template["bottom_colors"]
    colors = Image.composite(Image.new("RGB", (WIDTH, size), bottom_color),
                             Image.new("RGB", (WIDTH, size), top_color), gradient)
    filled = Image.new("RGB", image.size, bottom_color)
    filled.paste(Image.new("RGB", (WIDTH, top), top_color), (0, 0))
    filled.paste(colors, (0, top))
    image.paste(filled, (0, 0), mask)


def draw_watermark(image, text, font_path):
    draw = ImageDraw.Draw(image)
    font = load_font(font_path, 34)
    draw.text((WIDTH - 20, HEIGHT - 10), text, font=font, fill=(60, 60, 60), anchor="rd",
              stroke_width=3, stroke_fill="white")
