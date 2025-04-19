import streamlit as st
import requests
import hashlib
import sqlite3
import time
from datetime import datetime, timedelta

# --- API 설정 ---
NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPlFrlNqhZFuQ"
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
def send_message(text, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        res = requests.get(url, params=params)
        print("텔레그램 전송 상태:", res.status_code)
        return res
    except Exception as e:
        print("❌ 전송 중 예외:", e)

# --- 초기 설정 ---
init_db()
st.set_page_config(page_title="뉴스 모니터링 시스템", layout="wide")
st.title("📰 뉴스 모니터링 자동화 시스템")

# --- 상태 관리 ---
if "monitoring" not in st.session_state:
    st.session_state.monitoring = False
if "next_run" not in st.session_state:
    st.session_state.next_run = datetime.now()

# --- 키워드 입력 ---
keywords_input = st.text_input("키워드를 쉼표로 입력하세요", "ChatGPT,삼성전자")

col1, col2 = st.columns(2)

# --- 시작 버튼 ---
if col1.button("🟢 자동 실행 시작", disabled=st.session_state.monitoring):
    st.session_state.monitoring = True
    st.session_state.next_run = datetime.now()
    st.success("자동 실행 시작됨")

# --- 중지 버튼 ---
if col2.button("🔴 자동 실행 정지", disabled=not st.session_state.monitoring):
    st.session_state.monitoring = False
    st.warning("자동 실행 중지됨")

# --- 모니터링 로직 실행 ---
log_lines = []
if st.session_state.monitoring and datetime.now() >= st.session_state.next_run:
    keywords = [k.strip() for k in keywords_input.split(",")]

    for kw in keywords:
        news = search_news_naver(kw)
        new_items = []

        for item in news:
            h = make_hash(item["title"])
            if not is_sent(h):
                new_items.append(f"{item['title']}\n{item['link']}")
                mark_as_sent(h)

        if new_items:
            combined_msg = f"[{kw}] 새로운 뉴스\n" + "\n\n".join(new_items)
            send_message(combined_msg, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
            log_lines.append(f"[{kw}] {len(new_items)}개 전송됨")
        else:
            log_lines.append(f"[{kw}] 새 뉴스 없음")

    st.session_state.next_run = datetime.now() + timedelta(seconds=60)

# --- 로그 출력 ---
st.markdown("#### 📜 전송 로그")
if log_lines:
    st.code("\n".join(log_lines))
else:
    st.code("아직 전송된 뉴스가 없습니다. 1분마다 확인합니다.")
