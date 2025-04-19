import streamlit as st
import requests
import datetime
import time
import hashlib

# --- API 키 설정 ---
NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"
NEWS_API_KEY = "3a33b7b756274540926aeea8df60637c"

# --- 텔레그램 설정 ---
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPllFrlNqhZFuQ"
TELEGRAM_CHAT_ID = "-1002404027768"

# --- 저장용 임시 캐시 ---
sent_news_hash = set()

# --- 해시 생성 (중복 방지용) ---
def make_hash(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

# --- Naver 뉴스 검색 함수 ---
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

# --- 메인 로직 실행 ---
def run_news_monitor(keywords):
    new_items = []
    for kw in keywords:
        news_items = search_news_naver(kw)
        for item in news_items:
            news_hash = make_hash(item["title"])
            if news_hash not in sent_news_hash:
                message = f"<b>{kw}</b>\n{item['title']}\n{item['link']}"
                send_to_telegram(message)
                sent_news_hash.add(news_hash)
                new_items.append(item)
    return new_items

# --- Streamlit UI ---
st.title("📰 키워드 뉴스 모니터링 & 텔레그램 전송")
st.markdown("지정한 키워드에 대한 최신 뉴스를 수집하고 텔레그램으로 전송합니다.")

keywords_input = st.text_input("🔍 키워드 입력 (쉼표로 구분)", "삼성전자,ChatGPT")
if st.button("뉴스 확인 및 전송"):
    keywords = [kw.strip() for kw in keywords_input.split(",")]
    results = run_news_monitor(keywords)
    if results:
        st.success(f"{len(results)}건의 새로운 뉴스가 전송되었습니다.")
        for r in results:
            st.write(f"- [{r['title']}]({r['link']})")
    else:
        st.info("새로운 뉴스가 없습니다.")

st.caption("🧠 [GPT Online에서 더 많은 AI 자동화 앱 확인하기](https://gptonline.ai/ko/)")
