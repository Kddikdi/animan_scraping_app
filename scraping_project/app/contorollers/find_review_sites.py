"""保存した感想サイトから、指定した話の画像を参考用に集める

    ・作品とサイトのURLは 感想サイト/data.json に保存してある
    ・画像は依頼された話の分だけ 参考画像/<作品>/<サイト>/<N>話 画像/ に保存する。動画の素材（コマ画像）とは分ける
    ・記事に出典が書かれた画像は、その中の「出典 102巻」のようなサブフォルダに分ける
    ・白黒のコマに色付きの文字等を重ねた加工画像は保存しない（カラーページは残す）
    ・記事が見つからないときは理由を判定する。更新をやめたサイトは過去のサイト（past_sites）に移して
      候補（candidates）から差し替える。過去のサイトも最後に更新していた話までは使う
"""
import json
import os
import re
import time
import unicodedata
from datetime import date
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from fetch_images import IMAGE_EXTENSIONS, allowed_by_robots
from fetch_images import main as fetch_page_images
from .create_animan_video import safe_file_name

DATA_FILE = os.path.join("感想サイト", "data.json")
REFERENCE_IMAGE_DIR = "参考画像"

REQUEST_INTERVAL_SEC = 3
# 同じ話の記事が感想と考察で分かれているサイトがあるため、1話につき複数の記事を読む
MAX_ARTICLES = 3
TOP_SITES = 2
MAX_CHAPTERS = 10
CATEGORY_PAGES = 10
# 先の展開の予想（「1199話以降考察」「1198話展開予想」）、発売前の流出情報、アニメの話数は、漫画のその話の記事として扱わない
NOT_CHAPTER_ARTICLE = re.compile(r"\d+\s*話\s*(?:以降|以後)|予想|早バレ|確定速報|アニメ")
# 話数の書き方（「55話」「第55話」「#55」）
CHAPTER_NUMBER = re.compile(r"(?<!\d)(\d+)\s*話|#\s*(\d+)(?!\d)")
# 公開済みの最新話からこの話数以上遅れているサイトは更新をやめたとみなす
STALE_GAP = 3
# 記事はあるのに画像がない回がこの回数続いたサイトは、文章だけのサイトとみなして外す
NO_IMAGE_LIMIT = 2


def load_works():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)["works"]


def save_works(works):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"works": works}, f, ensure_ascii=False, indent=2)


def parse_chapters(text):
    """「129」「129 130」「129-131」「最新」形式の話数を読む。最新はNoneで返す"""
    text = unicodedata.normalize("NFKC", text)
    chapters = [None] if "最新" in text else []
    for start, end in re.findall(r"(\d+)(?:\s*[-~〜]\s*(\d+))?", text):
        chapters += range(int(start), int(end or start) + 1)
    chapters = list(dict.fromkeys(chapters))
    if not chapters:
        raise ValueError("話数を入力してください（例: 129、129-131、最新）")
    if len(chapters) > MAX_CHAPTERS:
        raise ValueError(f"一度に指定できるのは{MAX_CHAPTERS}話までです")
    return chapters


def fetch_listing(url):
    """記事の一覧ページを取得する。禁止されたページや存在しないページならNoneを返す"""
    if not allowed_by_robots(url):
        return None
    response = requests.get(url, timeout=30)
    time.sleep(REQUEST_INTERVAL_SEC)
    if response.status_code in (403, 429):
        raise PermissionError(f"サイトに拒否されました(HTTP {response.status_code})。回避はしません: {url}")
    return BeautifulSoup(response.text, "html.parser") if response.status_code == 200 else None


def same_site(url, site):
    return (urlparse(url).hostname or "").removeprefix("www.") == site


def compact(text):
    """表記ゆれ（全角半角・大小文字・空白）をそろえる"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


_site_titles = {}
_site_bases = {}


def site_base(site):
    """サイトのURL。www.付きでしか証明書が通らないサイトがあるので、つながらなければwww.付きにする"""
    if site not in _site_bases:
        _site_bases[site] = f"https://{site}"
        try:
            requests.head(f"https://{site}/", timeout=30)
        except requests.exceptions.SSLError:
            _site_bases[site] = f"https://www.{site}"
    return _site_bases[site]


def site_title(site):
    """サイトのトップページのタイトル（サイト名）。検索結果のページは検索語をタイトルに含むので使わない"""
    if site not in _site_titles:
        soup = fetch_listing(f"{site_base(site)}/")
        _site_titles[site] = soup.title.get_text() if soup is not None and soup.title else ""
    return _site_titles[site]


def work_names(title):
    """作品名と別名（data.jsonのaliases）"""
    work = next((work for work in load_works() if work["title"] == title), {})
    return [compact(name) for name in [title] + work.get("aliases", [])]


def linked_chapters(soup, base_url, site, title):
    """リンク文字に作品名と話数を含む同じサイトの記事を (話数, URL) で返す

    サイト名に作品名が入っている専門サイトは、記事タイトルに作品名を書かないことが多いので、話数だけで拾う
    """
    names = work_names(title)
    dedicated = any(name in compact(site_title(site)) for name in names)
    chapters = []
    for link in soup.find_all("a", href=True):
        text = unicodedata.normalize("NFKC", link.get_text(" ", strip=True))
        url = urljoin(base_url, link["href"]).split("#")[0]
        named = dedicated or any(name in compact(text) for name in names)
        if named and same_site(url, site) and not NOT_CHAPTER_ARTICLE.search(text):
            chapters += [(int(number or hashed), url) for number, hashed in CHAPTER_NUMBER.findall(text)]
    return chapters


def match_articles(soup, base_url, site, title, chapter):
    """リンク文字に作品名と話数の両方を含む、同じサイトの記事を返す"""
    found = linked_chapters(soup, base_url, site, title)
    return [url for number, url in found if number == chapter]


def pick_articles(articles, chapter):
    articles = list(dict.fromkeys(articles))
    # 前後の話の記事がタイトルで触れている場合があるので、URLに話数があるサイトではそれを優先する
    numbered = [url for url in articles if re.search(rf"(?<!\d){chapter}(?!\d)", urlparse(url).path)]
    return (numbered or articles)[:MAX_ARTICLES]


def find_articles(site, title, chapter):
    """保存したサイトの中から指定の話の記事を探す"""
    base = site_base(site)
    # WordPressのサイト内検索。「作品名 N話」では引っかからない記事があるので、話数だけでも検索して合わせる
    articles = []
    for query in (f"{title} {chapter}話", str(chapter)):
        url = f"{base}/?s={quote_plus(query)}"
        soup = fetch_listing(url)
        articles += match_articles(soup, url, site, title, chapter) if soup else []
    if articles:
        return pick_articles(articles, chapter)
    # Blogger等のサイト内検索
    url = f"{base}/search?q={quote_plus(f'{title} {chapter}話')}"
    soup = fetch_listing(url)
    articles = match_articles(soup, url, site, title, chapter) if soup else []
    if articles:
        return pick_articles(articles, chapter)
    # はてなブログは検索できないことがあるため、作品名のカテゴリ一覧をたどる
    url = f"{base}/archive/category/{quote(title)}"
    for _ in range(CATEGORY_PAGES):
        soup = fetch_listing(url)
        if soup is None:
            break
        articles = match_articles(soup, url, site, title, chapter)
        if articles:
            return pick_articles(articles, chapter)
        next_page = soup.select_one("a[rel=next], .pager-next a")
        if next_page is None:
            break
        url = urljoin(url, next_page["href"])
    return []


def latest_chapter(site, title):
    """サイトの作品の一覧から、掲載されている最新の話数を返す。判定できなければNone"""
    base = site_base(site)
    urls = (f"{base}/?s={quote_plus(title)}", f"{base}/search?q={quote_plus(title)}",
            f"{base}/archive/category/{quote(title)}")
    numbers = []
    for url in urls:
        soup = fetch_listing(url)
        if soup:
            numbers += [number for number, _ in linked_chapters(soup, url, site, title)]
    return max(numbers, default=None)


def chapter_folder(title, site, chapter):
    return os.path.join(REFERENCE_IMAGE_DIR, safe_file_name(title), safe_file_name(site), f"{chapter}話 画像")


def has_images(folder):
    """出典ごとのサブフォルダも含めて画像があるか"""
    return any(name.lower().endswith(IMAGE_EXTENSIONS) for _, _, names in os.walk(folder) for name in names)


def collect_chapter_images(title, chapters):
    """保存したサイトから、指定の話の画像をサイト・話ごとのフォルダに保存し、結果の文を返す"""
    works = load_works()
    work = next((work for work in works if work["title"] == title), None)
    if work is None:
        raise ValueError(f"作品が登録されていません: {title}")
    released, latest, messages = refresh_sites(work)
    save_works(works)
    if None in chapters and released is None:
        raise ValueError("最新話を判定できませんでした。話数を指定してください")
    for chapter in dict.fromkeys(released if chapter is None else chapter for chapter in chapters):
        messages += collect_chapter(work, chapter, released, latest)
        save_works(works)
    return messages


def refresh_sites(work):
    """保存サイトと候補の掲載最新話から公開済みの最新話を求め、更新をやめたサイトを差し替える

    サイト2つが同時に止まっていても気づけるよう、候補のサイトも確認する。
    戻り値は (公開済みの最新話, {サイト: 掲載最新話}, 結果の文)
    """
    title = work["title"]
    latest = {}
    for site in work["sites"] + [site for site in work["candidates"] if site not in work["sites"]]:
        try:
            latest[site] = latest_chapter(site, title)
        except (PermissionError, requests.RequestException):
            latest[site] = None
    released = max((number for number in latest.values() if number is not None), default=None)
    if released is None:
        return None, latest, []
    messages = []
    for site in list(work["sites"]):
        if latest[site] is not None and released - latest[site] >= STALE_GAP:
            work["sites"].remove(site)
            work["past_sites"].append({"site": site, "last_chapter": latest[site], "retired_on": date.today().isoformat()})
            messages.append(f"{site}: {latest[site]}話で更新が止まっています（公開済みは{released}話まで）。過去のサイトに移しました")
    return released, latest, messages + fill_sites(work, latest, released)


def fill_sites(work, latest, released):
    """サイトが2つに満たなければ、更新が続いている候補（公開済みの最新話から遅れがSTALE_GAP未満）で埋める"""
    messages = []
    for candidate in list(work["candidates"]):
        if len(work["sites"]) >= TOP_SITES:
            break
        if candidate not in work["sites"] and latest.get(candidate) is not None and released - latest[candidate] < STALE_GAP:
            work["candidates"].remove(candidate)
            work["sites"].append(candidate)
            messages.append(f"{candidate} に差し替えました（{latest[candidate]}話まで掲載）")
    if len(work["sites"]) < TOP_SITES:
        messages.append("差し替えられる候補がありません。サイトの再調査を依頼してください")
    return messages


def collect_chapter(work, chapter, released, latest):
    title = work["title"]
    messages = []
    missing = []
    found = False
    sites = list(work["sites"])
    for site in sites:
        result, status = collect_from_site(site, title, chapter)
        messages.append(f"{site} {chapter}話: {result}")
        found = found or status in ("found", "saved", "no_images")
        if status == "missing":
            missing.append(site)
        if count_no_images(work, site, status):
            # 文章だけのサイトを外し、差し替えたサイトからも同じ話を集める
            messages.append(f"{site}: 記事に画像が{NO_IMAGE_LIMIT}回続けてありませんでした。画像のないサイトとして外しました")
            replaced = fill_sites(work, latest, released) if released is not None else []
            messages += replaced
            sites += [site for site in work["sites"] if site not in sites]
    # 更新をやめたサイトも、更新していた話までは使う
    for past in work["past_sites"]:
        if chapter <= past["last_chapter"]:
            result, _ = collect_from_site(past["site"], title, chapter)
            messages.append(f"{past['site']} {chapter}話（過去のサイト）: {result}")
    for site in missing:
        messages.append(f"{site} {chapter}話: {missing_reason(chapter, latest.get(site), released, found)}")
    return messages


def count_no_images(work, site, status):
    """記事に画像がなかった回数を数え、NO_IMAGE_LIMIT回続いたらサイトを外してTrueを返す"""
    counts = work.setdefault("no_image_counts", {})
    if status == "found":
        counts.pop(site, None)
    if status != "no_images":
        return False
    counts[site] = counts.get(site, 0) + 1
    if counts[site] < NO_IMAGE_LIMIT:
        return False
    del counts[site]
    work["sites"].remove(site)
    work.setdefault("excluded_sites", []).append(
        {"site": site, "reason": "記事に画像がない", "excluded_on": date.today().isoformat()})
    return True


def missing_reason(chapter, site_latest, released, found):
    """記事が見つからなかった理由を返す。更新をやめたサイトはrefresh_sitesで差し替え済み"""
    if site_latest is None:
        return "このサイトでは掲載中の話数を判定できません"
    if site_latest >= chapter:
        return f"{site_latest}話までは掲載があります。タイトルの形式違いか、この話だけ記事がない可能性があります"
    if not found and chapter > released:
        return f"まだ公開されていない可能性があります（各サイトの最新は{released}話）"
    return f"更新待ちです（このサイトは{site_latest}話まで）"


def collect_from_site(site, title, chapter):
    """結果の文と状態（saved、found、no_images、missing、stopped）を返す"""
    folder = chapter_folder(title, site, chapter)
    if has_images(folder):
        return "保存済みです", "saved"
    try:
        articles = find_articles(site, title, chapter)
        if not articles:
            return "記事が見つかりませんでした", "missing"
        saved = sum(len(fetch_page_images(url, folder, by_source=True, skip_overlay=True)) for url in articles)
    except (PermissionError, requests.RequestException) as e:
        return f"取得を止めました ({e})", "stopped"
    return f"{len(articles)}記事から{saved}枚を保存しました", "found" if saved else "no_images"
