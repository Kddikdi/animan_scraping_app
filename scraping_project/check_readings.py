"""台本の各行を、VOICEVOXがどう読むか（カタカナ）を一覧にする

使い方: python check_readings.py 台本名.csv [--voicevox http://127.0.0.1:50021]

    ・読みの辞書（共通の読みと、作品の読み）を登録してから調べるので、動画にしたときと同じ読みになる
    ・読み間違いを見つけたら、感想サイト/data.json の作品の readings（作品に関係ない語は reading_dict.py の COMMON_READINGS）に足す
    ・キャラの別名辞書（characters）にあって読みが未登録の、漢字を含む名前は、VOICEVOXの読みと一緒に先頭に出す
    ・結果は 台本ファイル/<台本名>.読み.txt にも出る
"""
import argparse
import os
import re

import requests

from app.contorollers import reading_dict
from app.contorollers.create_animan_video import SCRIPT_DIR, read_meta, read_script
from app.contorollers.find_review_sites import load_works
from app.contorollers.image_reading import compact


def main(csv_name, voicevox_url="http://127.0.0.1:50021"):
    csv_path = csv_name if os.path.isfile(csv_name) else os.path.join(SCRIPT_DIR, csv_name)
    meta = read_meta(csv_path) or {}
    readings = {**reading_dict.COMMON_READINGS, **reading_dict.work_readings(meta.get("theme", ""))}
    reading_dict.register(voicevox_url, readings)
    rows = read_script(csv_path)
    script_text = " ".join(text for _, text, _, _ in rows)
    lines = []
    unregistered = [name for name in work_names(meta.get("theme", ""))
                    if KANJI.search(name) and name in script_text and name not in readings]
    if unregistered:
        lines.append("■ 読みが未登録の名前（VOICEVOXの読み）")
        for name in unregistered:
            lines.append(f"  {name}  {kana(voicevox_url, name)}")
        lines.append("")
    for number, (_, text, _, _) in enumerate(rows, 1):
        query = requests.post(f"{voicevox_url}/audio_query", params={"text": reading_dict.speech_text(text), "speaker": 3},
                              timeout=60)
        query.raise_for_status()
        lines.append(f"{number} {text}\n    {query.json()['kana']}")
    report_path = os.path.splitext(csv_path)[0] + ".読み.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"読みの一覧: {report_path}")


KANJI = re.compile(r"[一-龥々]")


def work_names(theme):
    """作品のキャラの正式名と別名（data.jsonのcharacters）"""
    flat = compact(theme)
    for work in load_works():
        if any(compact(name) in flat for name in [work["title"]] + work.get("aliases", [])):
            return list(dict.fromkeys(alias for name, aliases in work.get("characters", {}).items()
                                      for alias in [name] + aliases))
    return []


def kana(voicevox_url, text):
    query = requests.post(f"{voicevox_url}/audio_query", params={"text": text, "speaker": 3}, timeout=60)
    query.raise_for_status()
    return query.json()["kana"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台本の読みを一覧にする")
    parser.add_argument("csv_name")
    parser.add_argument("--voicevox", default="http://127.0.0.1:50021")
    args = parser.parse_args()
    main(args.csv_name, args.voicevox)
