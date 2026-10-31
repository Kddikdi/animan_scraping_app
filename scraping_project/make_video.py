"""コマンドラインから動画を作成する

例:
    python make_video.py 黒ひげのこの表情.csv --background bg.png --bgm bgm.mp3
    python make_video.py 黒ひげのこの表情.csv --silent   # VOICEVOXなしでレイアウト確認
"""
import argparse

from app.contorollers.create_animan_video import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台本CSVから反応集動画を作成")
    parser.add_argument("csv", help="台本ファイル名（台本ファイルフォルダ内）またはパス")
    parser.add_argument("--background", help="背景画像")
    parser.add_argument("--bgm", help="BGM音声ファイル")
    parser.add_argument("--voicevox-url", default="http://127.0.0.1:50021")
    parser.add_argument("--speed", type=float, default=1.2, help="読み上げ速度")
    parser.add_argument("--bgm-volume", type=float, default=0.12)
    parser.add_argument("--silent", action="store_true", help="音声合成せずに作成（レイアウト確認用）")
    parser.add_argument("--background-video", help="背景動画（反応集の台本のみ）")
    args = parser.parse_args()
    main(args.csv, args.background, args.bgm, args.voicevox_url, args.speed, args.bgm_volume, args.silent,
         args.background_video)
