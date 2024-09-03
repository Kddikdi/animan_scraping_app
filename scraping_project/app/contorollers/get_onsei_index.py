from bs4 import BeautifulSoup
import time
import requests
from app.models import Voice, db


def main():
    # 既存のデータを全て削除
    db.session.query(Voice).delete()
    db.session.commit()

    url = "https://voicevox.hiroshiba.jp/"
    html = requests.get(url)
    soup = BeautifulSoup(html.text, 'html.parser')
    time.sleep(3)
    onsei_name = []
    main = soup.find_all(class_ = "card-content has-text-centered")
    for a in main :
          a_tag = a.find("a").get_text()
          # データベースに音声名を登録
          new_voice = Voice(name=a_tag)
          db.session.add(new_voice)
    db.session.commit()



