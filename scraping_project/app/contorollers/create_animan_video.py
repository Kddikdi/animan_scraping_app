"""台本CSV（create_animan_scriptで作成）から反応集動画(mp4)を作成する

処理の流れ
    1. 台本CSV(音声名, セリフ)を読み込む
    2. 1行ごとにVOICEVOXエンジンで音声(wav)を合成
    3. 1行ごとに画面画像(png)をPillowで描画
    4. ffmpegで画像と音声を結合し、BGMを重ねてmp4出力（字幕srt・サムネイルも出力）

VOICEVOXエンジン(アプリ)を起動しておく必要がある（既定: http://127.0.0.1:50021）
silent=True の場合は音声合成をせず、文字数から表示時間を推定する（レイアウト確認用）
"""
import csv
import io
import os
import shutil
import subprocess
import tempfile
import wave

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 出力先・入力元
SCRIPT_DIR = "台本ファイル"
VIDEO_DIR = "出力動画"

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
         speed=1.2, bgm_volume=0.12, silent=False):
    """台本CSVから動画を作成し、出力したmp4のパスを返す"""
    csv_path = csv_name if os.path.isfile(csv_name) else os.path.join(SCRIPT_DIR, csv_name)
    rows = read_script(csv_path)
    if not rows:
        raise ValueError("台本が空です")
    title = rows[0][1]

    ffmpeg = find_ffmpeg()
    font_path = find_font()
    background = make_background(background_path)

    # 長文を分割して1画面ごとのページにする
    pages = make_pages(rows)
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
        encode_video(ffmpeg, list_path, audio_path, bgm_path, bgm_volume, output_path)
        write_srt(base_name + ".srt", segments)
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
            rows.append((row[0].strip(), row[1].strip()))
    return rows


def make_pages(rows):
    """台本の各行を画面単位に変換する（1行目はタイトルコール）"""
    pages = []
    for index, (voice, text) in enumerate(rows):
        if index == 0:
            pages.append({"kind": "title", "voice": voice, "text": text, "res_no": 0})
            continue
        chunks = split_text(text, MAX_CHARS_PER_PAGE)
        for chunk_index, chunk in enumerate(chunks):
            pages.append({
                "kind": "res",
                "voice": voice,
                "text": chunk,
                "res_no": index,
                "continued": chunk_index > 0,
            })
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


def write_concat_list(list_path, segments):
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for image_path, duration, _ in segments:
            f.write(f"file '{os.path.basename(image_path)}'\nduration {duration:.3f}\n")
        # concat demuxerは最後の画像を再度指定しないと最終durationが無視される
        f.write(f"file '{os.path.basename(segments[-1][0])}'\n")


def encode_video(ffmpeg, list_path, audio_path, bgm_path, bgm_volume, output_path):
    command = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path, "-i", audio_path]
    if bgm_path:
        command += ["-stream_loop", "-1", "-i", bgm_path,
                    "-filter_complex",
                    f"[2:a]volume={bgm_volume}[bgm];[1:a][bgm]amix=inputs=2:duration=first:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]"]
    else:
        command += ["-map", "0:v", "-map", "1:a"]
    command += ["-r", str(FPS), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-shortest", output_path]
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


def safe_file_name(name):
    for char in '\\/:*?"<>|':
        name = name.replace(char, "_")
    return name.strip() or "animan_video"
