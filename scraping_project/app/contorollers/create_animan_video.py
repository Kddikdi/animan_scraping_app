"""台本CSV（create_animan_scriptで作成）から反応集動画(mp4)を作成する

処理の流れ
    1. 台本CSV(音声名, セリフ)を読み込む
    2. 1行ごとにVOICEVOXエンジンで音声(wav)を合成
    3. 1行ごとに画面画像(png)をPillowで描画
    4. ffmpegで画像と音声を結合し、BGMを重ねてmp4出力（字幕srt・サムネイルも出力）

VOICEVOXエンジン(アプリ)を起動しておく必要がある（既定: http://127.0.0.1:50021）
silent=True の場合は音声合成をせず、文字数から表示時間を推定する（レイアウト確認用）

台本に設定ファイル(.meta.json)があれば反応集レイアウト（中央にコマ、下部に字幕）で作成し、
元スレURLを先頭に書いた説明文も出力する（create_reaction_scriptで作成）
反応集では背景に動画を指定でき、画面を透過で描画してループ再生する動画に重ねる
"""
import csv
import io
import json
import os
import random
import shutil
import subprocess
import tempfile
import wave

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import thumbnail_template

# 出力先・入力元
SCRIPT_DIR = "台本ファイル"
VIDEO_DIR = "出力動画"
PANEL_DIR = "コマ画像"
BACKGROUND_VIDEO_DIR = "背景動画"
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
BOARD_NAME = "あにまん掲示板"

# 画面設定
WIDTH, HEIGHT = 1920, 1080
FPS = 30
TITLE_BAND_HEIGHT = 120
BOX_MARGIN_X = 140
BOX_TOP = 200
BOX_BOTTOM = 960
MAX_FONT_SIZE = 72
MIN_FONT_SIZE = 44
# 1画面に収まらない長文はこの文字数を目安に分割する
MAX_CHARS_PER_PAGE = 140

# 反応集レイアウト
SUBTITLE_BAND_TOP = 790
SUBTITLE_MARGIN_X = 80
MAX_SUBTITLE_FONT_SIZE = 84
MAX_SUBTITLE_CHARS = 80
# レスごとに切り替える字幕の色（文字色, 縁取り色）
SUBTITLE_COLORS = [
    ((255, 235, 0), (0, 0, 0)),
    ((235, 30, 40), (255, 255, 255)),
    ((40, 110, 245), (255, 255, 255)),
    ((190, 50, 230), (255, 255, 255)),
    ((255, 255, 255), (0, 0, 0)),
]

# YouTubeのサムネイルの容量上限
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024

# 音声設定
PAUSE_SEC = 0.4  # セリフ間の無音
SILENT_CHARS_PER_SEC = 7  # 無音モード時の読み上げ速度の目安
SAMPLE_RATE = 24000

# 話者ごとの色（登場順に割り当て）
SPEAKER_COLORS = [
    (233, 84, 107),
    (76, 175, 80),
    (66, 133, 244),
    (255, 152, 0),
    (156, 39, 176),
    (0, 150, 136),
    (121, 85, 72),
    (96, 125, 139),
]

FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    # Mac
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]

KINSOKU_HEAD = "、。，．・：；？！ー）」』】〕〉》’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ…,.!?)]"


def main(csv_name, background_path=None, bgm_path=None, voicevox_url="http://127.0.0.1:50021",
         speed=1.2, bgm_volume=0.12, silent=False, background_video=None):
    """台本CSVから動画を作成し、出力したmp4のパスを返す"""
    csv_path = csv_name if os.path.isfile(csv_name) else os.path.join(SCRIPT_DIR, csv_name)
    rows = read_script(csv_path)
    if not rows:
        raise ValueError("台本が空です")
    meta = read_meta(csv_path)
    title = meta["title"] if meta else rows[0][1]

    ffmpeg = find_ffmpeg()
    font_path = find_font()
    background = make_background(background_path)
    # 背景動画を重ねるので画面は透過で描画する
    background_video = background_video if meta else None
    page_background = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)) if background_video else background

    # 長文を分割して1画面ごとのページにする
    pages = make_pages(rows, MAX_SUBTITLE_CHARS if meta else MAX_CHARS_PER_PAGE)
    colors = assign_colors(pages)

    tts = None if silent else VoicevoxClient(voicevox_url, speed)

    os.makedirs(VIDEO_DIR, exist_ok=True)
    base_name = os.path.join(VIDEO_DIR, safe_file_name(title))
    work_dir = tempfile.mkdtemp(prefix="animan_video_")
    try:
        segments = []  # (画像パス, 表示秒数, セリフ)
        audio_frames = []
        for i, page in enumerate(pages):
            # 音声
            if tts:
                pcm = tts.synthesize(page["voice"], page["text"])
            else:
                pcm = silence(max(1.5, len(page["text"]) / SILENT_CHARS_PER_SEC))
            pcm += silence(PAUSE_SEC)
            audio_frames.append(pcm)
            duration = len(pcm) / 2 / SAMPLE_RATE

            # 画像
            if meta:
                image = render_reaction_page(page, page_background, font_path, meta.get("copyright", ""))
            else:
                image = render_page(page, title, background, font_path, colors)
            image_path = os.path.join(work_dir, f"{i:04d}.png")
            image.save(image_path)
            segments.append((image_path, duration, page["text"]))
            print(f"{i + 1}/{len(pages)} 作成")

        audio_path = os.path.join(work_dir, "voice.wav")
        write_wav(audio_path, b"".join(audio_frames))
        list_path = os.path.join(work_dir, "images.txt")
        write_concat_list(list_path, segments)

        output_path = base_name + ".mp4"
        encode_video(ffmpeg, list_path, audio_path, bgm_path, bgm_volume, output_path, background_video)
        write_srt(base_name + ".srt", segments)
        if meta:
            save_thumbnail(csv_path)
            write_description(base_name + "_説明文.txt", meta, list(colors))
        else:
            render_thumbnail(title, background, font_path).save(base_name + "_thumbnail.png")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    print(f"動画作成完了しました: {output_path}")
    return output_path


def list_scripts():
    """台本ファイル一覧（新しい順）"""
    if not os.path.isdir(SCRIPT_DIR):
        return []
    files = [f for f in os.listdir(SCRIPT_DIR) if f.endswith(".csv")]
    return sorted(files, key=lambda f: os.path.getmtime(os.path.join(SCRIPT_DIR, f)), reverse=True)


def read_script(csv_path):
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[1].strip():
                continue
            # 3列目以降（コマ画像, 話題）は反応集の台本のみ
            panel, topic = (row + ["", ""])[2:4]
            rows.append((row[0].strip(), row[1].strip(), panel.strip(), topic.strip()))
    return rows


def read_meta(csv_path):
    meta_path = os.path.splitext(csv_path)[0] + ".meta.json"
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def write_meta(csv_path, meta):
    with open(os.path.splitext(csv_path)[0] + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def make_pages(rows, max_chars=MAX_CHARS_PER_PAGE):
    """台本の各行を画面単位に変換する（1行目はタイトルコール）"""
    pages = []
    for index, (voice, text, panel, topic) in enumerate(rows):
        common = {"voice": voice, "res_no": index, "panel": panel, "topic": topic}
        if index == 0:
            pages.append({"kind": "title", "text": text, **common})
            continue
        chunks = split_text(text, max_chars)
        for chunk_index, chunk in enumerate(chunks):
            pages.append({"kind": "res", "text": chunk, "continued": chunk_index > 0, **common})
    return pages


def split_text(text, max_chars):
    if len(text) <= max_chars:
        return [text]
    chunks = []
    current = ""
    # 句読点・空白の直後で区切る
    for char in text:
        current += char
        if len(current) >= max_chars * 0.6 and char in "。！？!? 　":
            chunks.append(current.strip())
            current = ""
        elif len(current) >= max_chars:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return chunks


def assign_colors(pages):
    colors = {}
    for page in pages:
        if page["voice"] not in colors:
            colors[page["voice"]] = SPEAKER_COLORS[len(colors) % len(SPEAKER_COLORS)]
    return colors


# ---------------------------------------------------------------- 音声

class VoicevoxClient:
    def __init__(self, url, speed):
        self.url = url.rstrip("/")
        self.speed = speed
        try:
            speakers = requests.get(f"{self.url}/speakers", timeout=10).json()
        except requests.RequestException:
            raise RuntimeError(f"VOICEVOXエンジンに接続できません({self.url})。VOICEVOXを起動してください")
        # キャラクター名 → スタイルID（ノーマル優先）
        self.speaker_ids = {}
        for speaker in speakers:
            styles = speaker["styles"]
            normal = [s for s in styles if s["name"] == "ノーマル"]
            self.speaker_ids[speaker["name"]] = (normal or styles)[0]["id"]
        self.default_ids = list(self.speaker_ids.values())
        self.fallback = {}

    def speaker_id(self, voice):
        if voice in self.speaker_ids:
            return self.speaker_ids[voice]
        # 「男1」など未登録の名前は登場順にVOICEVOXの話者を割り当てる
        if voice not in self.fallback:
            self.fallback[voice] = self.default_ids[len(self.fallback) % len(self.default_ids)]
        return self.fallback[voice]

    def synthesize(self, voice, text):
        speaker = self.speaker_id(voice)
        query = requests.post(f"{self.url}/audio_query", params={"text": text, "speaker": speaker}, timeout=60)
        query.raise_for_status()
        query_json = query.json()
        query_json["speedScale"] = self.speed
        query_json["outputSamplingRate"] = SAMPLE_RATE
        query_json["outputStereo"] = False
        query_json["prePhonemeLength"] = 0.05
        query_json["postPhonemeLength"] = 0.05
        audio = requests.post(f"{self.url}/synthesis", params={"speaker": speaker}, json=query_json, timeout=120)
        audio.raise_for_status()
        with wave.open(io.BytesIO(audio.content)) as w:
            return w.readframes(w.getnframes())


def silence(seconds):
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


# ---------------------------------------------------------------- 画像

def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError("日本語フォントが見つかりません。FONT_CANDIDATESにフォントのパスを追加してください")


def make_background(background_path):
    if background_path:
        image = Image.open(background_path).convert("RGB")
        # 画面いっぱいに拡大して中央を切り抜き
        scale = max(WIDTH / image.width, HEIGHT / image.height)
        image = image.resize((int(image.width * scale) + 1, int(image.height * scale) + 1))
        left = (image.width - WIDTH) // 2
        top = (image.height - HEIGHT) // 2
        image = image.crop((left, top, left + WIDTH, top + HEIGHT))
        image = image.filter(ImageFilter.GaussianBlur(4))
        return Image.blend(image, Image.new("RGB", image.size, (0, 0, 0)), 0.35)
    # 背景未指定時はグラデーション
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        draw.line([(0, y), (WIDTH, y)], fill=(int(30 + 20 * ratio), int(40 + 10 * ratio), int(70 - 30 * ratio)))
    return image


def wrap_text(draw, text, font, max_width):
    lines = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if draw.textlength(current + char, font=font) > max_width and current:
            # 行頭禁則：句読点などは前の行にぶら下げる
            if char in KINSOKU_HEAD:
                lines.append(current + char)
                current = ""
                continue
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def fit_text(draw, text, font_path, max_width, max_height, max_size=MAX_FONT_SIZE):
    """枠に収まる最大フォントサイズで折り返す"""
    for size in range(max_size, MIN_FONT_SIZE - 1, -4):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(draw, text, font, max_width)
        line_height = int(size * 1.45)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
    return font, lines, line_height


def draw_title_band(draw, title, font_path):
    draw.rectangle([0, 0, WIDTH, TITLE_BAND_HEIGHT], fill=(20, 20, 20))
    draw.rectangle([0, TITLE_BAND_HEIGHT - 8, WIDTH, TITLE_BAND_HEIGHT], fill=(220, 40, 60))
    size = 56
    font = ImageFont.truetype(font_path, size)
    while draw.textlength(title, font=font) > WIDTH - 80 and size > 28:
        size -= 4
        font = ImageFont.truetype(font_path, size)
    draw.text((40, (TITLE_BAND_HEIGHT - 8) // 2), title, font=font, fill="white", anchor="lm")


def render_page(page, title, background, font_path, colors):
    if page["kind"] == "title":
        return render_title_card(page["text"], background, font_path)

    image = background.copy()
    draw = ImageDraw.Draw(image)
    draw_title_band(draw, title, font_path)

    color = colors[page["voice"]]
    box = [BOX_MARGIN_X, BOX_TOP, WIDTH - BOX_MARGIN_X, BOX_BOTTOM]
    # 影 → 枠
    draw.rounded_rectangle([box[0] + 10, box[1] + 12, box[2] + 10, box[3] + 12], radius=28, fill=(0, 0, 0))
    draw.rounded_rectangle(box, radius=28, fill=(255, 255, 255), outline=color, width=10)

    # レス番号ヘッダー
    header_font = ImageFont.truetype(font_path, 36)
    header = f"{page['res_no']}: 名無しのあにまんch"
    if page.get("continued"):
        header += "（続き）"
    draw.text((box[0] + 50, box[1] + 40), header, font=header_font, fill=color)
    draw.line([box[0] + 40, box[1] + 100, box[2] - 40, box[1] + 100], fill=(220, 220, 220), width=3)

    # 本文（枠内で上下中央）
    text_left = box[0] + 60
    text_width = box[2] - box[0] - 120
    area_top = box[1] + 120
    area_height = box[3] - area_top - 50
    font, lines, line_height = fit_text(draw, page["text"], font_path, text_width, area_height)
    y = area_top + max(0, (area_height - line_height * len(lines)) // 2)
    for line in lines:
        draw.text((text_left, y), line, font=font, fill=(30, 30, 30))
        y += line_height

    # 読み上げキャラ名
    name_font = ImageFont.truetype(font_path, 34)
    name_width = draw.textlength(page["voice"], font=name_font)
    tag = [box[2] - name_width - 70, box[3] - 30, box[2] - 20, box[3] + 34]
    draw.rounded_rectangle(tag, radius=18, fill=color)
    draw.text(((tag[0] + tag[2]) / 2, (tag[1] + tag[3]) / 2), page["voice"], font=name_font, fill="white", anchor="mm")
    return image


def load_panel(panel):
    """コマ画像を読み込む。未指定・読み込み不可ならNone"""
    if not panel:
        return None
    path = panel if os.path.isabs(panel) else os.path.join(PANEL_DIR, panel)
    try:
        return Image.open(path).convert("RGB")
    except OSError:
        print(f"コマ画像を読み込めません: {path}")
        return None


def draw_panel(image, draw, page, font_path):
    panel = load_panel(page["panel"])
    if panel:
        # 字幕帯より上に収まるように拡大縮小して中央に配置
        scale = min((WIDTH - 200) / panel.width, (SUBTITLE_BAND_TOP - 40) / panel.height)
        panel = panel.resize((int(panel.width * scale), int(panel.height * scale)), Image.LANCZOS)
        image.paste(panel, ((WIDTH - panel.width) // 2, (SUBTITLE_BAND_TOP - panel.height) // 2))
    elif page["topic"]:
        # コマが無いうちは話題名を表示する
        font, lines, line_height = fit_text(draw, page["topic"], font_path, WIDTH - 400, 400, 120)
        y = (SUBTITLE_BAND_TOP - line_height * len(lines)) // 2 + line_height // 2
        for line in lines:
            draw.text((WIDTH // 2, y), line, font=font, fill="white", anchor="mm",
                      stroke_width=6, stroke_fill="black")
            y += line_height


def render_reaction_page(page, background, font_path, copyright_text):
    image = background.convert("RGBA")
    draw = ImageDraw.Draw(image)
    draw_panel(image, draw, page, font_path)

    band = Image.new("RGBA", (WIDTH, HEIGHT - SUBTITLE_BAND_TOP), (0, 0, 0, 215))
    image.alpha_composite(band, (0, SUBTITLE_BAND_TOP))

    fill, stroke = SUBTITLE_COLORS[page["res_no"] % len(SUBTITLE_COLORS)]
    font, lines, line_height = fit_text(draw, page["text"], font_path, WIDTH - SUBTITLE_MARGIN_X * 2,
                                        HEIGHT - SUBTITLE_BAND_TOP - 50, MAX_SUBTITLE_FONT_SIZE)
    y = (SUBTITLE_BAND_TOP + HEIGHT - line_height * len(lines)) // 2 + line_height // 2
    for line in lines:
        draw.text((WIDTH // 2, y), line, font=font, fill=fill, anchor="mm", stroke_width=5, stroke_fill=stroke)
        y += line_height

    # 規約により動画内の先頭に掲示板名を記載する
    if page["kind"] == "title":
        source_font = ImageFont.truetype(font_path, 56)
        source_text = f"引用元：『{BOARD_NAME}』"
        box = draw.textbbox((24, 16), source_text, font=source_font)
        draw.rectangle([box[0] - 12, box[1] - 10, box[2] + 12, box[3] + 10], fill=(0, 0, 0))
        draw.text((24, 16), source_text, font=source_font, fill="white")

    if copyright_text:
        copyright_font = ImageFont.truetype(font_path, 32)
        draw.text((WIDTH - 24, 16), copyright_text, font=copyright_font, fill="white", anchor="ra",
                  stroke_width=3, stroke_fill="black")
    return image


def render_title_card(title, background, font_path):
    image = background.copy()
    draw = ImageDraw.Draw(image)
    band_top, band_bottom = HEIGHT // 2 - 220, HEIGHT // 2 + 220
    draw.rectangle([0, band_top, WIDTH, band_bottom], fill=(20, 20, 20))
    draw.rectangle([0, band_top, WIDTH, band_top + 12], fill=(220, 40, 60))
    draw.rectangle([0, band_bottom - 12, WIDTH, band_bottom], fill=(220, 40, 60))
    font, lines, line_height = fit_text(draw, title, font_path, WIDTH - 240, band_bottom - band_top - 80, 120)
    y = HEIGHT // 2 - line_height * len(lines) // 2 + line_height // 2
    for line in lines:
        draw.text((WIDTH // 2, y), line, font=font, fill="white", anchor="mm")
        y += line_height
    return image


def render_thumbnail(title, background, font_path):
    image = background.copy()
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, 200], fill=(220, 40, 60))
    label_font = ImageFont.truetype(font_path, 120)
    draw.text((WIDTH // 2, 100), "反応集", font=label_font, fill="white", anchor="mm")
    font = ImageFont.truetype(font_path, 130)
    lines = wrap_text(draw, title, font, WIDTH - 160)
    while len(lines) > 4 and font.size > 60:
        font = ImageFont.truetype(font_path, font.size - 10)
        lines = wrap_text(draw, title, font, WIDTH - 160)
    line_height = int(font.size * 1.25)
    y = 200 + (HEIGHT - 200 - line_height * len(lines)) // 2 + line_height // 2
    for line in lines:
        draw.text((WIDTH // 2, y), line, font=font, fill="yellow", anchor="mm",
                  stroke_width=10, stroke_fill="black")
        y += line_height
    return image


# ---------------------------------------------------------------- 動画

def find_ffmpeg():
    path = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpegが見つかりません。インストールしてPATHを通すか、環境変数FFMPEG_PATHを設定してください")
    return path


def list_background_videos():
    if not os.path.isdir(BACKGROUND_VIDEO_DIR):
        return []
    return sorted(f for f in os.listdir(BACKGROUND_VIDEO_DIR) if f.lower().endswith((".mp4", ".mov", ".webm")))


def resolve_background_video(choice, keyword=""):
    """画面の選択内容から背景動画のパスを返す。キーワードがあればPexelsから取得する"""
    if keyword:
        return download_pexels_video(keyword)
    videos = list_background_videos()
    if choice == "random" and videos:
        choice = random.choice(videos)
    return os.path.join(BACKGROUND_VIDEO_DIR, choice) if choice in videos else None


def download_pexels_video(keyword):
    """Pexelsで横長の動画を検索して背景動画フォルダに保存し、パスを返す"""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数PEXELS_API_KEYを設定してください（pexels.comで無料取得）")
    response = requests.get(PEXELS_SEARCH_URL, headers={"Authorization": api_key}, timeout=30,
                            params={"query": keyword, "orientation": "landscape", "per_page": 15})
    response.raise_for_status()
    videos = response.json()["videos"]
    if not videos:
        raise ValueError(f"Pexelsに動画が見つかりませんでした: {keyword}")
    video = random.choice(videos)
    # 1080pに近いmp4を選ぶ
    files = [f for f in video["video_files"] if f.get("file_type") == "video/mp4" and f.get("height")]
    best = min(files, key=lambda f: abs(f["height"] - HEIGHT))

    os.makedirs(BACKGROUND_VIDEO_DIR, exist_ok=True)
    path = os.path.join(BACKGROUND_VIDEO_DIR, f"pexels_{video['id']}.mp4")
    if not os.path.isfile(path):
        with requests.get(best["link"], stream=True, timeout=60) as download:
            download.raise_for_status()
            with open(path, "wb") as f:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
    return path


def list_reaction_scripts():
    """反応集の台本（設定ファイルがあるもの）の一覧"""
    return [f for f in list_scripts() if read_meta(os.path.join(SCRIPT_DIR, f))]


def list_panels(title):
    """動画のコマ画像フォルダの画像一覧（コマ画像フォルダからの相対パス）"""
    folder = safe_file_name(title)
    if not os.path.isdir(os.path.join(PANEL_DIR, folder)):
        return []
    return [f"{folder}/{name}" for name in sorted(os.listdir(os.path.join(PANEL_DIR, folder)))]


def thumbnail_settings(csv_path):
    """保存済みのサムネイル設定を返す。無ければ台本から初期値を作る"""
    meta = read_meta(csv_path)
    return meta.get("thumbnail") or thumbnail_template.default_settings(read_script(csv_path), meta)


def add_thumbnail_image(csv_path, file):
    """アップロードされた画像をコマ画像フォルダに保存し、相対パスを返す"""
    folder = safe_file_name(read_meta(csv_path)["title"])
    os.makedirs(os.path.join(PANEL_DIR, folder), exist_ok=True)
    Image.open(file).convert("RGB").save(os.path.join(PANEL_DIR, folder, "thumbnail_upload.jpg"), quality=95)
    return f"{folder}/thumbnail_upload.jpg"


def save_thumbnail(csv_path, settings=None):
    """サムネイルを出力し、ファイル名を返す。settingsを渡すと台本の設定として保存する"""
    meta = read_meta(csv_path)
    if settings:
        meta["thumbnail"] = settings
        write_meta(csv_path, meta)
    settings = thumbnail_settings(csv_path)

    panel_path = None
    if settings["image"]:
        path = os.path.join(PANEL_DIR, settings["image"])
        if os.path.isfile(path):
            panel_path = path
        else:
            print(f"コマ画像を読み込めません: {path}")
    image = thumbnail_template.render_thumbnail(settings, panel_path)

    os.makedirs(VIDEO_DIR, exist_ok=True)
    name = safe_file_name(meta["title"]) + "_thumbnail.jpg"
    path = os.path.join(VIDEO_DIR, name)
    for quality in (95, 90, 85, 80, 70):
        image.save(path, quality=quality)
        if os.path.getsize(path) <= MAX_THUMBNAIL_BYTES:
            break
    return name


def write_concat_list(list_path, segments):
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for image_path, duration, _ in segments:
            f.write(f"file '{os.path.basename(image_path)}'\nduration {duration:.3f}\n")
        # concat demuxerは最後の画像を再度指定しないと最終durationが無視される
        f.write(f"file '{os.path.basename(segments[-1][0])}'\n")


def encode_video(ffmpeg, list_path, audio_path, bgm_path, bgm_volume, output_path, background_video=None):
    command = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path, "-i", audio_path]
    filters = []
    video_map, audio_map = "0:v", "1:a"
    next_input = 2
    if bgm_path:
        command += ["-stream_loop", "-1", "-i", bgm_path]
        filters.append(f"[{next_input}:a]volume={bgm_volume}[bgm];[1:a][bgm]amix=inputs=2:duration=first:normalize=0[a]")
        audio_map = "[a]"
        next_input += 1
    if background_video:
        # 背景動画をループして画面いっぱいに切り抜き、透過の画面を重ねる
        command += ["-stream_loop", "-1", "-i", background_video]
        filters.append(f"[{next_input}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                       f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}[bg];"
                       f"[0:v]format=rgba,fps={FPS}[fg];[bg][fg]overlay=shortest=1[v]")
        video_map = "[v]"
    if filters:
        command += ["-filter_complex", ";".join(filters)]
    command += ["-map", video_map, "-map", audio_map, "-r", str(FPS), "-c:v", "libx264"]
    command += ["-preset", "veryfast", "-crf", "23"] if background_video else ["-tune", "stillimage"]
    command += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", output_path]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpegでエラーが発生しました: {result.stderr}")


def write_srt(path, segments):
    def timestamp(seconds):
        ms = int(round(seconds * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    start = 0.0
    with open(path, "w", encoding="utf-8") as f:
        for index, (_, duration, text) in enumerate(segments, 1):
            end = start + duration - PAUSE_SEC
            f.write(f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n")
            start += duration


def write_description(path, meta, voices):
    """YouTube説明文を出力する。規約により元スレURLは必ず先頭に全て記載する"""
    lines = list(meta["source_urls"])
    lines += ["", "チャンネル登録・高評価よろしくお願いします！", "※この動画はネタバレを含みます", "",
              "【引用元】", BOARD_NAME]
    if meta.get("copyright"):
        lines += ["", "【動画内の引用作品】", meta["copyright"]]
    lines += ["", "【著作権に関しまして】",
              "動画で掲載している画像などの著作権や肖像権等は全てその権利所有者様に帰属致します。",
              "動画の内容に問題がある場合は、各権利所有者様本人からご連絡頂けましたら幸いです。"]
    lines += ["", "【音声】"] + [f"VOICEVOX:{voice}" for voice in voices]
    if meta.get("hashtags"):
        lines += ["", meta["hashtags"]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def safe_file_name(name):
    for char in '\\/:*?"<>|':
        name = name.replace(char, "_")
    return name.strip() or "animan_video"
