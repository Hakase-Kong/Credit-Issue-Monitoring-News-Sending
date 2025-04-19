import streamlit as st
import requests, hashlib, sqlite3, time, threading

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

def is_sent(news_hash):
    conn = sqlite3.connect('news.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_news WHERE hash=?", (news_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_as_sent(news_hash):
    conn = sqlite3.connect('news.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sent_news (hash) VALUES (?)", (news_hash,))
    conn.commit()
    conn.close()

def make_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

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

def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    res = requests.get(url, params=params)
    print("텔레그램 응답:", res.status_code, res.text)
    return res

def monitor_loop(keywords, stop_event):
    while not stop_event.is_set():
        all_msgs = []
        log_lines = []

        for kw in keywords:
            news = search_news_naver(kw)
            items_to_send = []
            for item in news:
                h = make_hash(item["title"])
                if not is_sent(h):
                    items_to_send.append(f"🔸 <a href='{item['link']}'>{item['title']}</a>")
                    mark_as_sent(h)
                    log_lines.append(f"[{kw}] 전송됨: {item['title']}")

            if items_to_send:
                combined = f"<b>[{kw}] 뉴스</b>\n" + "\n".join(items_to_send)
                all_msgs.append(combined)

        if all_msgs:
            send_message("\n\n".join(all_msgs))
        else:
            log_lines.append("새 뉴스 없음.")

        # 👉 UI에서 읽어갈 수 있도록 session_state에 저장
        st.session_state["log_text"] = "\n".join(log_lines)
        time.sleep(60)

# --- Streamlit UI ---
init_db()
st.title("📰 뉴스 모니터링 자동화 시스템")

keywords_input = st.text_input("키워드를 쉼표로 입력하세요", "ChatGPT,삼성전자")

if "monitoring" not in st.session_state:
    st.session_state.monitoring = False
if "stop_event" not in st.session_state:
    st.session_state.stop_event = threading.Event()
if "log_text" not in st.session_state:
    st.session_state.log_text = ""

col1, col2 = st.columns(2)
if col1.button("🟢 자동 실행 시작", disabled=st.session_state.monitoring):
    keywords = [k.strip() for k in keywords_input.split(",")]
    st.session_state.stop_event.clear()
    threading.Thread(target=monitor_loop, args=(keywords, st.session_state.stop_event), daemon=True).start()
    st.session_state.monitoring = True

if col2.button("🛑 자동 실행 정지", disabled=not st.session_state.monitoring):
    st.session_state.stop_event.set()
    st.session_state.monitoring = False

# 🪵 로그 출력 계속 갱신
log_area = st.empty()
while True:
    log_area.markdown(f"```\n{st.session_state['log_text']}\n```")
    time.sleep(1)
