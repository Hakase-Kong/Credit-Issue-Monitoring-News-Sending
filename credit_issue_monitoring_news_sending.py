import streamlit as st
import requests
import hashlib
import sqlite3
import time
import threading

# --- API 설정 ---
NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPllFrlNqhZFuQ"
TELEGRAM_CHAT_ID = "-1002404027768"

# --- DB 초기화 ---
def init_db():
    conn = sqlite3.connect('news.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS sent_news (hash TEXT PRIMARY KEY)')
    conn.commit()
    conn.close()

# --- 뉴스 전송 여부 확인 ---
def is_sent(news_hash):
    conn = sqlite3.connect('news.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_news WHERE hash=?", (news_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

# --- 뉴스 전송 처리 ---
def mark_as_sent(news_hash):
    conn = sqlite3.connect('news.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sent_news (hash) VALUES (?)", (news_hash,))
    conn.commit()
    conn.close()

# --- 뉴스 해시 생성 ---
def make_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

# --- 뉴스 검색 ---
def search_news_naver(keyword):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {"query": keyword, "display": 5, "sort": "date"}
    response = requests.get(url, headers=headers, params=params)
    items = response.json().get("items", [])
    return [{"title": item["title"], "link": item["link"]} for item in items]

# --- 텔레그램 전송 ---
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    return requests.get(url, params=params)

# --- 모니터링 루프 ---
def monitor_loop(keywords, stop_event):
    while not stop_event.is_set():
        all_msgs = []
        log_text = ""

        for kw in keywords:
            news = search_news_naver(kw)
            new_items = []
            for item in news:
                h = make_hash(item["title"])
                if not is_sent(h):
                    new_items.append(f"🔸 <a href='{item['link']}'>{item['title']}</a>")
                    mark_as_sent(h)
                    log_text += f"✅ {kw}: {item['title']}\n"
            if new_items:
                msg = f"<b>[{kw}] 최신 뉴스</b>\n" + "\n".join(new_items)
                all_msgs.append(msg)

        if all_msgs:
            combined_msg = "\n\n".join(all_msgs)
            send_message(combined_msg)
        else:
            log_text = "새 뉴스 없음. 대기 중..."

        log_area.markdown(f"```\n{log_text}\n```")
        time.sleep(60)

    status_area.warning("🛑 모니터링이 중지되었습니다.")

# --- Streamlit UI ---
init_db()
st.title("📰 뉴스 모니터링 자동화 시스템")

keywords_input = st.text_input("키워드를 쉼표로 입력하세요", "ChatGPT,삼성전자")
log_area = st.empty()
status_area = st.empty()

if "monitoring" not in st.session_state:
    st.session_state.monitoring = False
if "stop_event" not in st.session_state:
    st.session_state.stop_event = threading.Event()

# 시작 버튼
if not st.session_state.monitoring and st.button("🟢 자동 실행 시작"):
    keywords = [k.strip() for k in keywords_input.split(",")]
    st.session_state.stop_event.clear()
    t = threading.Thread(target=monitor_loop, args=(keywords, st.session_state.stop_event), daemon=True)
    t.start()
    st.session_state.monitoring = True
    status_area.success("자동 실행 시작됨 (1분 주기)")

# 정지 버튼
if st.session_state.monitoring and st.button("🛑 자동 실행 정지"):
    st.session_state.stop_event.set()
    st.session_state.monitoring = False
    status_area.info("정지 요청됨. 다음 루프 종료 시 중지됩니다.")
