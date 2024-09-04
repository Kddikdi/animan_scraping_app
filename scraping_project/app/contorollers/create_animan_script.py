# from selenium import webdriver  # seleniumからwebdriverをインポート
# from webdriver_manager.chrome import ChromeDriverManager
# from selenium.webdriver.chrome.service import Service
import requests
import pandas as pd
from bs4 import BeautifulSoup
import time
import os
import webbrowser
import csv


def main(url,title_voice, narration_voices):
    # 対象ページを別タブで開く
    webbrowser.open(url, new=1)
    # ChromeDriverのサービスを設定
    # driver = webdriver.Chrome(service=service, options=options)
    # 初期変数設定
    # url = "https://bbs.animanch.com/board/3710616/"  #←url代入されるため不要
    csv_title = ""  # 台本抽出にて自動作成
    # driver.get(url)
    time.sleep(3)
    html = requests.get(url)
    soup = BeautifulSoup(html.text, 'html.parser')

    name = []
    script = []
    # 台本抽出
    script, csv_title = make_script(soup)
    # 音声割り当て
    name = make_talk_name(script, title_voice, narration_voices)
    # 長さチェック
    # name_count = len(name)
    # script_count = len(script)
    # 出力df作成
    script_df = pd.DataFrame({"name": name, "script": script})
    # CSV出力
    output_csv(csv_title, script_df)


def make_script(soup):
    main = soup.find(id="catalog")
    csv_title = main.find(id="threadTitle").text
    reslist_list = []
    # タイトル追加
    reslist_list.append(csv_title)
    reslist_list_main = main.find(id="reslist")
    # reslist_list_body=reslist_list_main.find_all("p")
    # for script in  reslist_list_body:
    #     if script.find("a") is None:
    #         script_body=script.get_text()
    #         # 全角空白削除＊要編集
    #         # script_body=script_body.replace('\u3000', ' ')
    #         reslist_list.append(script_body)
    reslist_list_li = reslist_list_main.find_all("li")

    for script_main in reslist_list_li:
        scripts = script_main.find_all("p")
        reslist = []
        if scripts is None or not scripts:
            continue
        # １レスの抽出（複数行、改行している場合は半角開けて結合、削除レスは追加しない）
        for script in scripts:
            if script.find("a") is None:
                script_body = script.get_text(separator=' ')
                if script_body == "このレスは削除されています":
                    break
                reslist.append(script_body)
        if reslist == []:
            continue
        result = " ".join(reslist)
        reslist_list.append(result)

    return reslist_list, csv_title


def make_talk_name(script, title_voice, narration_voices):
    name_list = []
    count = len(script)
    # 読み上げ音声を手動設定
    name_candidate = narration_voices
    candidate_count = len(name_candidate)
    # タイトル読み上げは固定
    call_title_name = title_voice
    name_list.append(call_title_name)

    for i in range(count-1):
        name_list.append(name_candidate[i % candidate_count])
    return name_list


def output_csv(csv_title, df):
    # 出力先ファイルが無ければ作成する
    dir = ".\台本ファイル"
    os.makedirs(dir, exist_ok=True)
    file_name = f'./台本ファイル/{csv_title}.csv'
    df.to_csv(file_name, index=False, header=False, encoding='utf-8-sig')
    print("ファイル作成完了しました")


# if __name__ == "__main__":
#     url = "https://bbs.animanch.com/board/3727369/"
#     main(url)