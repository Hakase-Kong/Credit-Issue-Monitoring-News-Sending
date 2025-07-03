import streamlit as st
import requests
import re
from datetime import datetime
import telepot

# --- 스타일 개선 ---
st.markdown("""
    <style>
        .block-container {padding-top: 2rem; padding-bottom: 2rem;}
        .stButton button {margin-top: 6px; margin-bottom: 6px; border-radius: 8px;}
        .stTextInput > div > div > input {font-size: 16px;}
        .stMultiSelect [data-baseweb="tag"] {
            background-color: #fff0f0 !important;
            color: #d60000 !important;
            border: 1px solid #d60000 !important;
        }
        .stMultiSelect label { color: #d60000 !important; font-weight: bold;}
        .stSelectbox, .stDateInput, .stMultiSelect {margin-bottom: 0.5rem;}
    </style>
""", unsafe_allow_html=True)

# --- API 키 설정 ---
NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"

# --- 텔레그램 설정 ---
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPlFrlNqhZFuQ"
TELEGRAM_CHAT_ID = "-1002404027768"

# --- 키워드 ---
credit_keywords = ["신용등급", "신용하향", "신용상향", "등급조정", "부정적", "긍정적", "평가"]
finance_keywords = ["적자", "흑자", "부채", "차입금", "현금흐름", "영업손실", "순이익", "부도", "파산"]
all_filter_keywords = sorted(set(credit_keywords + finance_keywords))
default_credit_issue_patterns = [
    "신용등급", "신용평가", "하향", "상향", "강등", "조정", "부도",
    "파산", "디폴트", "채무불이행", "적자", "영업손실", "현금흐름", "자금난",
    "재무위험", "부정적 전망", "긍정적 전망", "기업회생", "워크아웃", "구조조정", "자본잠식"
]

# --- 세션 상태 초기화 ---
if "search_results" not in st.session_state:
    st.session_state.search_results = {}
if "show_limit" not in st.session_state:
    st.session_state.show_limit = {}
if "expanded_keywords" not in st.session_state:
    st.session_state.expanded_keywords = set()
if "favorite_keywords" not in st.session_state:
    st.session_state.favorite_keywords = set()

class Telegram:
    def __init__(self):
        self.bot = telepot.Bot(token=TELEGRAM_TOKEN)
    def send_message(self, message):
        self.bot.sendMessage(TELEGRAM_CHAT_ID, message, parse_mode="Markdown")

def is_credit_risk_news(text, keywords):
    for word in keywords:
        if re.search(word, text, re.IGNORECASE):
            return True
    return False

def filter_by_issues(title, desc, selected_keywords, enable_credit_filter, credit_filter_keywords):
    content = title + " " + desc
    if enable_credit_filter and not is_credit_risk_news(content, credit_filter_keywords):
        return False
    return True

def fetch_naver_news(query, start_date=None, end_date=None, enable_credit_filter=True, credit_filter_keywords=None, limit=100):
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    articles = []
    for page in range(1, 6):
        if len(articles) >= limit:
            break
        params = {
            "query": query,
            "display": 10,
            "start": (page - 1) * 10 + 1,
            "sort": "date"
        }
        response = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, params=params)
        if response.status_code != 200:
            break
        items = response.json().get("items", [])
        for item in items:
            title, desc = item["title"], item["description"]
            pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z").date()
            if start_date and pub_date < start_date:
                continue
            if end_date and pub_date > end_date:
                continue
            if not filter_by_issues(title, desc, [], enable_credit_filter, credit_filter_keywords):
                continue
            articles.append({
                "title": re.sub("<.*?>", "", title),
                "link": item["link"],
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": "Naver"
            })
    return articles[:limit]

def fetch_gnews_news(query, enable_credit_filter=True, credit_filter_keywords=None, limit=100):
    GNEWS_API_KEY = "b8c6d82bbdee9b61d2b9605f44ca8540"
    articles = []
    try:
        url = f"https://gnews.io/api/v4/search"
        params = {
            "q": query,
            "lang": "en",
            "token": GNEWS_API_KEY,
            "max": limit
        }
        response = requests.get(url, params=params)
        if response.status_code != 200:
            st.warning(f"❌ GNews 요청 실패 - 상태 코드: {response.status_code}")
            return []
        data = response.json()
        for item in data.get("articles", []):
            title = item.get("title", "")
            desc = item.get("description", "")
            if not filter_by_issues(title, desc, [], enable_credit_filter, credit_filter_keywords):
                continue
            pub_date = datetime.strptime(item["publishedAt"][:10], "%Y-%m-%d").date()
            articles.append({
                "title": title,
                "link": item.get("url", ""),
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": "GNews"
            })
    except Exception as e:
        st.warning(f"⚠️ GNews 접근 오류: {e}")
    return articles

def render_articles_columnwise(results, show_limit):
    col_count = min(len(results), 4)
    cols = st.columns(col_count)
    for idx, (keyword, articles) in enumerate(results.items()):
        with cols[idx % col_count]:
            st.markdown(
                f"<span style='font-size:22px;font-weight:700;'>📁 {keyword}</span>",
                unsafe_allow_html=True
            )
            articles_to_show = articles[:show_limit.get(keyword, 5)]
            for article in articles_to_show:
                st.markdown(
                    f"""
                    <div style='margin-bottom: 12px; padding: 10px; border: 1px solid #eee; border-radius: 10px; background-color: #fafafa;'>
                        <div style='font-weight: bold; font-size: 15px; margin-bottom: 4px;'>
                            <a href="{article['link']}" target="_blank" style='text-decoration: none; color: #1155cc;'>
                                {article['title']}
                            </a>
                        </div>
                        <div style='font-size: 12px; color: gray;'>
                            {article['date']} | {article['source']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True
                )
            if len(articles) > show_limit.get(keyword, 5):
                if st.button("더보기", key=f"more_{keyword}", use_container_width=True):
                    st.session_state.show_limit[keyword] += 5
                    st.rerun()

def send_to_telegram(keyword, articles):
    if articles:
        msg = f"*[{keyword}] 관련 상위 뉴스 5건:*\n"
        for a in articles:
            title = re.sub(r"[\U00010000-\U0010ffff]", "", a['title'])
            msg += f"- [{title}]({a['link']})\n"
        try:
            Telegram().send_message(msg)
        except Exception as e:
            st.warning(f"텔레그램 전송 오류: {e}")

def is_english(text):
    return all(ord(c) < 128 for c in text if c.isalpha())

def process_keywords(keyword_list, start_date, end_date, enable_credit_filter, credit_filter_keywords):
    for k in keyword_list:
        if is_english(k):
            articles = fetch_gnews_news(k, enable_credit_filter, credit_filter_keywords)
        else:
            articles = fetch_naver_news(k, start_date, end_date, enable_credit_filter, credit_filter_keywords)
        st.session_state.search_results[k] = articles
        st.session_state.show_limit[k] = 5
        send_to_telegram(k, articles[:5])

# --- 요약 API 호출 함수 (자동 언어 감지 포함) ---
def summarize_article_from_url(article_url):
    try:
        # 언어 자동 감지용 API URL
        api_url = "https://article-extractor-and-summarizer.p.rapidapi.com/summarize"
        headers = {
            "x-rapidapi-key": "3558ef6abfmshba1bd48265c6fc4p101a63jsnb2c1ee3d33c4",
            "x-rapidapi-host": "article-extractor-and-summarizer.p.rapidapi.com"
        }

        # 자동 언어 감지 및 요약 수행
        lang = "ko" if any(ord(c) > 127 for c in article_url) else "en"
        params = {
            "url": article_url,
            "lang": lang,
            "engine": "2"
        }

        response = requests.get(api_url, headers=headers, params=params)
        response.raise_for_status()
        result = response.json()
        return result.get("summary", "요약 결과 없음"), result.get("text", "본문 없음")
    except Exception as e:
        return f"요약 오류: {e}", None

# --- 기사 카드 UI 수정: 요약 버튼 추가 ---
def render_articles_columnwise_with_summary(results, show_limit):
    col_count = min(len(results), 4)
    cols = st.columns(col_count)
    for idx, (keyword, articles) in enumerate(results.items()):
        with cols[idx % col_count]:
            st.markdown(
                f"<span style='font-size:22px;font-weight:700;'>📁 {keyword}</span>",
                unsafe_allow_html=True
            )
            articles_to_show = articles[:show_limit.get(keyword, 5)]
            for i, article in enumerate(articles_to_show):
                with st.container():
                    st.markdown(
                        f"""
                        <div style='margin-bottom: 10px; padding: 10px; border: 1px solid #eee; border-radius: 10px; background-color: #fafafa;'>
                            <div style='font-weight: bold; font-size: 15px; margin-bottom: 4px;'>
                                <a href="{article['link']}" target="_blank" style='text-decoration: none; color: #1155cc;'>
                                    {article['title']}
                                </a>
                            </div>
                            <div style='font-size: 12px; color: gray;'>
                                {article['date']} | {article['source']}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    # 요약 버튼
                    if st.button("요약", key=f"summary_{keyword}_{i}", use_container_width=True):
                        with st.spinner("기사 요약 중..."):
                            summary, full_text = summarize_article_from_url(article['link'])
                            if full_text:
                                st.markdown("<div style='font-size:14px; font-weight:bold;'>🔍 본문 요약:</div>", unsafe_allow_html=True)
                                st.write(summary)
                            else:
                                st.warning(summary)

            # 더보기 버튼
            if len(articles) > show_limit.get(keyword, 5):
                if st.button("더보기", key=f"more_{keyword}", use_container_width=True):
                    st.session_state.show_limit[keyword] += 5
                    st.rerun()


# --- Streamlit 설정 ---
st.set_page_config(layout="wide")
st.markdown("<h1 style='color:#1a1a1a; margin-bottom:0.5rem;'>📊 Credit Issue Monitoring</h1>", unsafe_allow_html=True)

# 1. 키워드 입력 및 버튼 한 줄에 정렬 (y축 맞춤 - 버튼 한 번만 내림)
col1, col2, col3 = st.columns([6, 1, 1])
with col1:
    keywords_input = st.text_input("키워드 (예: 삼성, 한화)", value="")
with col2:
    st.write("")  # y축 맞춤용 placeholder (1번)
    search_clicked = st.button("검색", use_container_width=True)
with col3:
    st.write("")  # y축 맞춤용 placeholder (1번)
    fav_add_clicked = st.button("⭐ 즐겨찾기 추가", use_container_width=True)
    if fav_add_clicked:
        new_keywords = {kw.strip() for kw in keywords_input.split(",") if kw.strip()}
        st.session_state.favorite_keywords.update(new_keywords)
        st.success("즐겨찾기에 추가되었습니다.")

# 2. 날짜 입력 두 칸에 정렬
date_col1, date_col2 = st.columns([1, 1])
with date_col1:
    start_date = st.date_input("시작일")
with date_col2:
    end_date = st.date_input("종료일")

# 3. 필터 옵션
with st.expander("🛡️ 신용위험 필터 옵션", expanded=True):
    enable_credit_filter = st.checkbox("신용위험 뉴스만 필터링", value=False)
    credit_filter_keywords = st.multiselect(
        "신용위험 관련 키워드 (하나 이상 선택)",
        options=default_credit_issue_patterns,
        default=default_credit_issue_patterns,
        key="credit_filter"
    )

# 4. 즐겨찾기 검색 영역 (y축 맞춤)
fav_col1, fav_col2 = st.columns([5, 1])
with fav_col1:
    fav_selected = st.multiselect("⭐ 즐겨찾기에서 검색", sorted(st.session_state.favorite_keywords))
with fav_col2:
    st.write("")  # y축 맞춤용 placeholder (1번)
    fav_search_clicked = st.button("즐겨찾기로 검색", use_container_width=True)

# 5. 검색 및 즐겨찾기 검색 처리
if search_clicked and keywords_input:
    keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()]
    if len(keyword_list) > 10:
        st.warning("키워드는 최대 10개까지 입력 가능합니다.")
    else:
        with st.spinner("뉴스 검색 중..."):
            process_keywords(keyword_list, start_date, end_date, enable_credit_filter, credit_filter_keywords)

if fav_search_clicked and fav_selected:
    with st.spinner("뉴스 검색 중..."):
        process_keywords(fav_selected, start_date, end_date, enable_credit_filter, credit_filter_keywords)

# 6. 뉴스 결과 카드 컬럼 정렬
if st.session_state.search_results:
    render_articles_columnwise_with_summary(st.session_state.search_results, st.session_state.show_limit)
