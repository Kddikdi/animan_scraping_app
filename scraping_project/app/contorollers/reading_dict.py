"""VOICEVOXの読み間違いを直す

    ・VOICEVOXのユーザー辞書に読みを登録する（字幕の表記は変わらない）。登録はVOICEVOX側に残る
    ・読みは、作品ごとのキャラ等（感想サイト/data.jsonのreadings）と、作品に関係ない共通の読み（COMMON_READINGS）
    ・スコア「3-2」のような、辞書では直せない表記は読み上げるときだけ書き換える
"""
import re
import unicodedata

import requests

# 作品に関係なく、スレでよく使われて読み間違いやすい語 {表記: 読み（カタカナ）}
COMMON_READINGS = {
    "草": "クサ",
    "頭脳戦": "ズノウセン",
    "VERSUS": "バーサス",
    "ノってる": "ノッテル",
    "パリッてる": "パリッテル",
    "〇〇": "マルマル",
    "奴": "ヤツ",
    "今記憶": "イマキオク",
}
# 辞書で一番優先する（ほかの読み方より必ずこちらを使う）
PRIORITY = 10
# スコア「3-2」。日付「2024-11-07」のようにハイフンが続くものは対象外
SCORE = re.compile(r"(?<![\d.\-])(\d{1,2})-(\d{1,2})(?![\d.\-])")


def speech_text(text):
    """読み上げるときだけ表記を書き換える（字幕はそのまま）"""
    return SCORE.sub(r"\1対\2", text)


def work_readings(theme):
    """テーマ（「ブルーロック367話」等）の作品の読み"""
    from .find_review_sites import load_works
    from .image_reading import compact

    flat = compact(theme)
    for work in load_works():
        if any(compact(name) in flat for name in [work["title"]] + work.get("aliases", [])):
            return work.get("readings", {})
    return {}


def register(voicevox_url, readings):
    """ユーザー辞書に読みを登録・更新し、変えた語の数を返す"""
    url = voicevox_url.rstrip("/")
    # VOICEVOXは英数字を全角にして保存するので、全角半角をそろえて比べる
    existing = {unicodedata.normalize("NFKC", word["surface"]): (word_id, word["pronunciation"])
                for word_id, word in requests.get(f"{url}/user_dict", timeout=10).json().items()}
    changed = 0
    for surface, pronunciation in readings.items():
        params = {"surface": surface, "pronunciation": pronunciation, "accent_type": 0,
                  "word_type": "PROPER_NOUN", "priority": PRIORITY}
        key = unicodedata.normalize("NFKC", surface)
        if key in existing:
            word_id, current = existing[key]
            if current == pronunciation:
                continue
            response = requests.put(f"{url}/user_dict_word/{word_id}", params=params, timeout=10)
        else:
            response = requests.post(f"{url}/user_dict_word", params=params, timeout=10)
        response.raise_for_status()
        changed += 1
    return changed
