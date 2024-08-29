from selenium import webdriver  # seleniumからwebdriverをインポート
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import pandas as pd
from bs4 import BeautifulSoup
import time
import urllib.parse
import os


def main(excel_title, sheet_title, stop_title, base_url, start_url):
    # ChromeDriverのサービスを設定
    service = Service(ChromeDriverManager().install())
    # Chromeのオプションを設定
    options = webdriver.ChromeOptions()
    # WebDriverを初期化
    driver = webdriver.Chrome(service=service, options=options)
    # 初期変数設定
    # excel_title = "onepiece_log"
    # sheet_title = ""
    # stop_title = "おれもこの時代に居たら"
    # base_url = "https://bbs.animanch.com/kakolog16" + "/"  # あにまんワンピース過去ログを設定
    # start_url = "https://bbs.animanch.com/kakolog16"  # 途中から開始したい場合URLを挿入
    # 初期変数編集
    if not base_url.endswith('/'):
        base_url += '/'
    time.sleep(3)  # 3秒待機

    # 出力df設定
    df = pd.DataFrame(columns=["Title", "count", "URL"])

    result = True
    count = 0
    while result:
        count += 1
        driver.get(start_url)  # driverがurlのページを開きます
        # HTML取得
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        # タイトル取得関数
        # thread_title, thread_count, thread_url = make_articleURL(soup)
        result_1, append_df = make_articleURL(soup, stop_title)
        # 出力dfの追加
        df = pd.concat([df, append_df])
        # append確認
        print(f"現在の取得数={len(df)}")

        # ページ遷移関数
        result_2, start_url = click_next(soup, base_url, start_url)
        if result_1 is False or result_2 is False:
            break

    # 処理終了後Excelに出力
    print("Excel出力を行います")
    output_excel(excel_title, sheet_title, df)
    # ドライバーを閉じる
    driver.quit()


def make_articleURL(soup, stop_title):
    # 記事はメインスレッド内
    main = soup.find(id="mainThread")

    # 格納リスト作成
    thread_title = []
    thread_count = []
    thread_url = []
    # 投稿数取得
    thread_count_body = main.find_all("p", class_="threadCount")
    for count in thread_count_body:
        thread_count.append(count.get_text())
        # print(count.get_text())

    # URL取得
    url_body = main.find_all("a", href=True)
    for url in url_body:
        thread_url.append(url["href"])
        # print(url["href"])

    # タイトル取得
    title_body = main.find_all("span", class_="title col-9 col-sm-10")
    for title in title_body:
        thread_title.append(title.get_text())
        # print(title.get_text())

    # 暫定df作成
    new_df = pd.DataFrame(columns=["Title", "count", "URL"])
    check = True
    if stop_title is None or stop_title == "":
        new_df = pd.DataFrame({"Title": thread_title, "count": thread_count, "URL": thread_url})
    else:
        rows = []
        i = 0
        for i in range(len(thread_title)):
            if thread_title[i] == stop_title:
                i = 1
                break
            else:
                row = {"Title": thread_title[i], "count": thread_count[i], "URL": thread_url[i]}
                rows.append(row)

        # rows リストからDataFrameを作成し、新しいDataFrameを作成
        additional_df = pd.DataFrame(rows)
        new_df = pd.concat([new_df, additional_df])
        if i == 1:
            check = False
    return check, new_df


def click_next(soup, base_url, current_url):
    # 次ページ要素を取得しクリック
    next_page_bodies = soup.find_all("li", class_="page-item angle")
    len_check = len(next_page_bodies)
    if len_check == 4:
        next_page = next_page_bodies[1].find("a", href=True)["href"]
        print(f"次ページ{next_page}")
        next_url = urllib.parse.urljoin(base_url, next_page)
        # print(current_url)
        # 3秒待機
        time.sleep(3)
        return True, next_url
    elif len_check == 2:
        # URLが1ページ目ならば次ページへ進む
        if base_url == current_url or base_url+"page:1" == current_url or base_url == current_url+"/":
            next_page = next_page_bodies[1].find("a", href=True)["href"]
            print(f"次ページ{next_page}")
            next_url = urllib.parse.urljoin(base_url, next_page)
            # print(current_url)
            # 3秒待機
            time.sleep(3)
            return True, next_url
        else:
            print("最終ページです")
            return False, current_url
    else:
        print("ページが見つかりません")
        return False, current_url


def output_excel(excel_title, sheet_title, df):
    # 50000件ごとに分割して出力
    N = 50000
    splited_df = [df.iloc[i:i+N-1, :] for i in range(0, len(df), N)]
    # 出力ファイルが無ければ作成する
    dir = ".\出力ファイル"
    os.makedirs(dir, exist_ok=True)
    for i, output_df in enumerate(splited_df):
        # f-string を使ってファイル名とシート名を設定
        file_name = f'.\出力ファイル/{excel_title}{i + 1}.xlsx'
        sheet_name = f'{sheet_title}{i + 1}'
        output_df.to_excel(file_name, sheet_name=sheet_name, index=False, startcol=1)
        print(f'{i+1}ファイル目を出力しました')


# if __name__ == "__main__":
#     main()
