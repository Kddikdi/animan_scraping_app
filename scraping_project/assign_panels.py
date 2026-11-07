"""反応集の台本のコマ画像を、感想サイトから集めた画像の中からコメントに合わせて割り当てる

使い方: python assign_panels.py 台本名.csv 作品名 話数 [--lines 2]
        python assign_panels.py 台本名.csv 作品名 話数 --sheet 出力フォルダ     （見直し用の一覧画像を作る）
        python assign_panels.py 台本名.csv 作品名 話数 --fix 3-4=12 5-6=7      （候補番号で差し替える）
        python assign_panels.py 台本名.csv 作品名 話数 --describe 3="サンジが…" （候補番号の画像に説明を書く）

    ・先に感想サイト画像収集で、その話の画像を集めておく
    ・2セリフ（--lines）ごとに1枚、コメントのキャラ名・語句・セリフに合う画像を選ぶ
    ・割り当ての理由は 台本ファイル/<台本名>.コマ割り当て.txt に出る
    ・--sheet は、その話と1つ前の話の画像に番号を振った一覧画像と、番号ごとの手がかり（<台本名>.コマ候補.txt）を作る
    ・--fix は「行範囲=候補番号」で差し替える。行番号は割り当ての一覧の「3-4行目」と同じ
    ・--describe は「候補番号=説明」で、画像に説明（誰が・どんな場面か・セリフ）を書く。割り当てで一番重く扱われ、蓄積される
"""
import argparse
import re

from app.contorollers import panel_matcher

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台本のコマ画像をコメントに合わせて割り当てる")
    parser.add_argument("csv_name")
    parser.add_argument("work_title")
    parser.add_argument("chapter", type=int)
    parser.add_argument("--lines", type=int, default=panel_matcher.LINES_PER_IMAGE, help="1枚の画像を表示するセリフ数")
    parser.add_argument("--sheet", help="見直し用の一覧画像の出力フォルダ")
    parser.add_argument("--fix", nargs="+", help="差し替え（例: 3-4=12）")
    parser.add_argument("--describe", nargs="+", help="画像の説明（例: 3=サンジがキリンガムの槍を素手で砕く）")
    args = parser.parse_args()
    if args.sheet:
        sheets, list_path = panel_matcher.contact_sheets(args.csv_name, args.work_title, args.chapter, args.sheet)
        print("\n".join(sheets + [list_path]))
    elif args.describe:
        descriptions = {}
        for item in args.describe:
            number, text = item.split("=", 1)
            descriptions[int(number)] = text.strip()
        print("\n".join(panel_matcher.describe_images(args.csv_name, args.work_title, args.chapter, descriptions)))
    elif args.fix:
        fixes = {}
        for item in args.fix:
            first, last, number = map(int, re.fullmatch(r"(\d+)-(\d+)=(\d+)", item).groups())
            fixes[(first, last)] = number
        print("\n".join(panel_matcher.fix_panels(args.csv_name, args.work_title, args.chapter, fixes)))
    else:
        panel_matcher.main(args.csv_name, args.work_title, args.chapter, args.lines)
