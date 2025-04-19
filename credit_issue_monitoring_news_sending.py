import streamlit as st
import requests
import datetime
import time
import hashlib
import threading

# --- API 키 설정 ---
NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPllFrlNqhZFuQ"
TELEGRAM_CHAT_ID = "-1002404027768"

sent_news_hash = set()

# --- 해시 생성 (중복 방지용) ---
def make_hash(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

# --- Naver 뉴스 검색 ---
def search_news_naver(keyword):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {
        "query": keyword,
        "display": 5,
        "sort": "date"
    }
    response = requests.get(url, headers=headers, params=params)
    items = response.json().get("items", [])
    return [{"title": item["title"], "link": item["link"]} for item in items]

# --- 텔레그램 전송 ---
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    requests.get(url, params=params)

# --- 뉴스 모니터링 로직 ---
def run_news_monitor(keywords):
    for kw in keywords:
        news_items = search_news_naver(kw)
        for item in news_items:
            news_hash = make_hash(item["title"])
            if news_hash not in sent_news_hash:
                message = f"<b>{kw}</b>\n{item['title']}\n{item['link']}"
                send_to_telegram(message)
                sent_news_hash.add(news_hash)

# --- 백그라운드 스레드: 1분마다 실행 ---
def schedule_news_monitor(keywords):
    while True:
        run_news_monitor(keywords)
        time.sleep(60)

# --- Streamlit UI ---
st.title("📰 키워드 뉴스 모니터링 자동 전송")
st.markdown("키워드에 해당하는 뉴스를 1분마다 수집하여 텔레그램으로 전송합니다.")

keywords_input = st.text_input("🔍 키워드 입력 (쉼표로 구분)", "삼성전자,ChatGPT")

if st.button("🟢 1분마다 자동 실행 시작"):
    keywords = [kw.strip() for kw in keywords_input.split(",")]
    thread = threading.Thread(target=schedule_news_monitor, args=(keywords,), daemon=True)
    thread.start()
    st.success("1분마다 뉴스 수집이 자동으로 실행됩니다. Streamlit 앱이 켜져있는 동안 유지됩니다.")

st.caption("🧠 [GPT Online에서 더 많은 AI 자동화 앱 확인하기](https://gptonline.ai/ko/)")
