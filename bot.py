import os
import json
import time
import html
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

# ==========================================
# [설정 영역] GitHub Secrets에서 안전하게 불러오기
# ==========================================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
HISTORY_FILE = "sent_ppomppu_hot.json"

# 한국 표준시 (KST = UTC+9) 고정
KST = timezone(timedelta(hours=9))

# 인기/HOT 필터링 전용 URL (&hotlist_flag=999)
BOARDS = [
    {
        "id": "ppomppu",
        "name": "🇰🇷 국내뽐뿌",
        "url": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu&hotlist_flag=999"
    },
    {
        "id": "ppomppu4",
        "name": "🌎 해외뽐뿌",
        "url": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu4&hotlist_flag=999"
    },
    {
        "id": "ppomppu8",
        "name": "🇨🇳 알리뽐뿌",
        "url": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu8&hotlist_flag=999"
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.ppomppu.co.kr/"
}


def now_kst():
    """서버 위치와 무관하게 항상 현재 한국 시간(KST) 반환"""
    return datetime.now(KST)


def load_sent_ids():
    """기존에 보낸 게시글 고유 키 목록 불러오기"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"⚠️ 기록 파일 로드 실패 (새로 시작): {e}")
    return set()


def save_sent_ids(sent_ids):
    """보낸 게시글 고유 키 목록 저장하기 (최근 1500개 유지)"""
    recent_ids = sorted(list(sent_ids))[-1500:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(recent_ids, f, ensure_ascii=False, indent=2)


def send_telegram_text(text):
    """텍스트 전용 텔레그램 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"⚠️ 텔레그램 전송 실패: {res.text}")
    except Exception as e:
        print(f"⚠️ 텔레그램 연결 오류: {e}")


def parse_post_datetime(date_td, current_kst):
    """뽐뿌 등록일 정보를 한국 시간(datetime)으로 변환"""
    title_attr = date_td.get("title", "").strip()
    if not title_attr and date_td.parent:
        title_attr = date_td.parent.get("title", "").strip()

    if title_attr:
        for fmt in ("%y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(title_attr, fmt)
                return dt.replace(tzinfo=KST)
            except ValueError:
                continue

    reg_date_str = date_td.get_text(strip=True)
    if ":" not in reg_date_str:
        return None

    try:
        parts = [int(x) for x in reg_date_str.split(":")]
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m = parts
            s = 0
        else:
            return None

        post_dt = current_kst.replace(hour=h, minute=m, second=s, microsecond=0)
        if post_dt > current_kst + timedelta(minutes=5):
            post_dt -= timedelta(days=1)

        return post_dt
    except ValueError:
        return None


def detect_hot_badge(row):
    """게시글 행(row)에서 배지 종류 판별"""
    title_tag = row.select_one("a.baseList-title")
    if not title_tag:
        return "🔥인기/HOT"
    title_cell = title_tag.find_parent("td") or row

    for tag in title_cell.find_all(["span", "em", "strong", "font", "div", "b"]):
        if tag.find_parent("a", class_="baseList-title"):
            continue
        txt = tag.get_text(strip=True).upper()
        if txt == "HOT":
            return "🔴HOT"
        if txt == "인기":
            return "🔵인기"

    for img in title_cell.find_all("img"):
        if img.find_parent("a", class_="baseList-thumb") or "thumb_border" in img.get("class", []):
            continue
        alt = img.get("alt", "").strip().upper()
        src = img.get("src", "").strip().lower()
        if "HOT" in alt or "hot" in src:
            return "🔴HOT"
        if "인기" in alt or "pop" in src or "best" in src or "inki" in src:
            return "🔵인기"

    return "🔥인기/HOT"


def fetch_board_posts(board, start_dt):
    """기준 시각(start_dt) 이후에 등록된 인기/HOT 글 수집"""
    response = requests.get(board["url"], headers=HEADERS, timeout=15)
    head_bytes = response.content[:500].lower()
    response.encoding = "utf-8" if b"utf-8" in head_bytes else "cp949"
    soup = BeautifulSoup(response.text, "html.parser")

    current_kst = now_kst()
    posts = []
    rows = soup.select("tr.baseList")

    for row in rows:
        num_td = row.select_one("td.baseList-numb")
        if not num_td or not num_td.text.strip().isdigit():
            continue
        post_no = num_td.text.strip()
        unique_key = f"{board['id']}_{post_no}"

        date_td = row.select_one("td.baseList-space time") or row.select_one("td.baseList-space") or row.select_one("td:nth-of-type(5)")
        if not date_td:
            continue
        post_dt = parse_post_datetime(date_td, current_kst)

        if post_dt is None or post_dt < start_dt:
            continue

        title_tag = row.select_one("a.baseList-title")
        if not title_tag:
            continue
        title = title_tag.text.strip()
        href = title_tag.get("href", "")
        link = f"https://www.ppomppu.co.kr/zboard/{href}" if not href.startswith("http") else href

        category_tag = row.select_one("span.baseList-category") or row.select_one("em.baseList-category")
        category = category_tag.text.strip() if category_tag else "일반"

        badge = detect_hot_badge(row)

        posts.append({
            "unique_key": unique_key,
            "post_no": int(post_no),
            "badge": badge,
            "board_name": board["name"],
            "category": category,
            "title": title,
            "link": link,
            "time": post_dt.strftime("%Y-%m-%d %H:%M:%S")
        })

    posts.sort(key=lambda x: x["post_no"])
    return posts


def fetch_all_boards(start_dt):
    """등록된 모든 게시판에서 기준 시각 이후 글 통합 수집"""
    all_posts = []
    for board in BOARDS:
        try:
            board_posts = fetch_board_posts(board, start_dt)
            all_posts.extend(board_posts)
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ [{board['name']}] 수집 중 오류: {e}")
    return all_posts


# ==========================================
# GitHub Actions 1회 실행 메인 로직
# ==========================================
if __name__ == "__main__":
    NOW_DT = now_kst()
    # 실행 시점 기준 2시간 전 이후 글 탐색 (뒤늦게 인기/HOT 승격된 글도 누락 없이 포착, 중복은 sent_ids로 차단)
    START_DT = NOW_DT - timedelta(hours=2)

    now_time_str = NOW_DT.strftime("%Y-%m-%d %H:%M:%S")
    start_time_str = START_DT.strftime("%Y-%m-%d %H:%M:%S")

    print(f"🚀 [{now_time_str} KST] 뽐뿌 [인기/HOT] 체크 시작 (탐색 기준: {start_time_str} 이후)")

    sent_ids = load_sent_ids()
    current_posts = fetch_all_boards(START_DT)
    new_posts = [p for p in current_posts if p["unique_key"] not in sent_ids]

    if new_posts:
        print(f"✨ 새로운 인기/HOT 딜 {len(new_posts)}건 발견! 텔레그램 전송 중...")
        for post in new_posts:
            safe_title = html.escape(post["title"])
            safe_category = html.escape(post["category"])
            message = (
                f"<b>[{post['badge']}] {post['board_name']}</b> ({safe_category})\n"
                f"<a href=\"{post['link']}\">{safe_title}</a>\n"
                f"⏰ 등록: {post['time']}"
            )
            send_telegram_text(message)
            sent_ids.add(post["unique_key"])
            time.sleep(1.0)

        save_sent_ids(sent_ids)
        print("💾 전송 기록(sent_ppomppu_hot.json) 저장 완료")
    else:
        print("💤 새로운 인기/HOT 딜 없음")
