"""反応集の台本のコマ画像を、感想サイトから集めた画像の中からコメントに合わせて割り当てる

使い方: python assign_panels.py 台本名.csv 作品名 話数 [--lines 2]

    ・先に感想サイト画像収集で、その話の画像を集めておく
    ・2セリフ（--lines）ごとに1枚、コメントのキャラ名・語句・セリフに合う画像を選ぶ
    ・割り当ての理由は 台本ファイル/<台本名>.コマ割り当て.txt に出る
"""
import argparse

from app.contorollers import panel_matcher

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台本のコマ画像をコメントに合わせて割り当てる")
    parser.add_argument("csv_name")
    parser.add_argument("work_title")
    parser.add_argument("chapter", type=int)
    parser.add_argument("--lines", type=int, default=panel_matcher.LINES_PER_IMAGE, help="1枚の画像を表示するセリフ数")
    args = parser.parse_args()
    panel_matcher.main(args.csv_name, args.work_title, args.chapter, args.lines)
