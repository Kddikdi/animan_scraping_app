"""手持ちの画像がコマとして採用されるかを一覧で確認する（収集時と同じ判定）

使い方: python check_panels.py 画像フォルダ [画像フォルダ ...]
"""
import os
import sys

from PIL import Image

from app.contorollers.create_reaction_script import MAX_PANEL_COLOR_RATIO, MIN_PANEL_SIDE, color_ratio


def main(folders):
    for folder in folders:
        for name in sorted(os.listdir(folder)):
            try:
                with Image.open(os.path.join(folder, name)) as image:
                    ratio = color_ratio(image)
                    small = min(image.size) < MIN_PANEL_SIDE
                    result = "不採用(小さい)" if small else "不採用(カラー)" if ratio > MAX_PANEL_COLOR_RATIO else "採用"
                    print(f"{result}\t色の割合{ratio:.1%}\t{image.size[0]}x{image.size[1]}\t{name}")
            except OSError:
                continue


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
