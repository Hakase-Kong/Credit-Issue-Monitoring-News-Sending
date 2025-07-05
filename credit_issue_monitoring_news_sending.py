import nltk

# 'punkt' 다운로드
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

# 'punkt_tab' 다운로드 (환경에 따라 필요)
try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')


import streamlit as st
import requests
import re
import os
from datetime import datetime
import telepot
from openai import OpenAI
import newspaper  # newspaper4k
from google.cloud import language_v1

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def detect_lang(text):
    return "ko" if re.search(r"[가-힣]", text) else "en"

def analyze_sentiment_google(text):
    lang = detect_lang(text)
    try:
        client_gc = language_v1.LanguageServiceClient()
        document = language_v1.Document(
            content=text,
            type_=language_v1.Document.Type.PLAIN_TEXT,
            language=lang
        )
        response = client_gc.analyze_sentiment(request={"document": document})
        score = response.document_sentiment.score
        if score > 0.05:
            return "긍정"
        elif score < -0.05:
            return "부정"
        else:
            return "중립"
    except Exception as e:
        return f"분석실패: {e}"

# --- newspaper4k로 기사 본문 추출 ---
def extract_article_text(url):
    try:
        article = newspaper.article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        return f"본문 추출 오류: {e}"

def summarize_with_openai(text):
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", None
    lang = detect_lang(text)
    if lang == "ko":
        prompt = (
            "아래 기사 본문을 3문장 이내로 요약해줘.\n"
            "단, 기사와 직접적으로 관련 없는 광고, 배너, 추천기사, 서비스 안내, 사이트 공통 문구 등은 모두 요약에서 제외해줘.\n"
            "기사의 핵심 내용만 요약해줘.\n\n"
            f"[기사 본문]\n{text}"
        )
    else:
        prompt = (
            "Summarize the following news article in 3 sentences.\n"
            "Exclude any content that is not directly related to the article itself, such as advertisements, banners, recommended articles, service notices, or site-wide generic messages.\n"
            "Focus only on the main content of the article.\n\n"
            f"[ARTICLE]\n{text}"
        )
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": prompt}
        ],
        max_tokens=256,
        temperature=0.3
    )
    summary = response.choices[0].message.content.strip()
    return summary, text

# --- 이하 기존 코드 동일 ---
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

NAVER_CLIENT_ID = "_qXuzaBGk_jQesRRPRvu"
NAVER_CLIENT_SECRET = "lZc2gScgNq"
TELEGRAM_TOKEN = "7033950842:AAFk4pSb5qtNj435Gf2B5-rPlFrlNqhZFuQ"
TELEGRAM_CHAT_ID = "-1002404027768"

class Telegram:
    def __init__(self):
        self.bot = telepot.Bot(TELEGRAM_TOKEN)
        self.chat_id = TELEGRAM_CHAT_ID

    def send_message(self, message):
        self.bot.sendMessage(self.chat_id, message, parse_mode="Markdown", disable_web_page_preview=True)

credit_keywords = ["신용등급", "신용하향", "신용상향", "등급조정", "부정적", "긍정적", "평가"]
finance_keywords = ["적자", "흑자", "부채", "차입금", "현금흐름", "영업손실", "순이익", "부도", "파산"]
all_filter_keywords = sorted(set(credit_keywords + finance_keywords))
default_credit_issue_patterns = [
    "신용등급", "신용평가", "하향", "상향", "강등", "조정", "부도",
    "파산", "디폴트", "채무불이행", "적자", "영업손실", "현금흐름", "자금난",
    "재무위험", "부정적 전망", "긍정적 전망", "기업회생", "워크아웃", "구조조정", "자본잠식"
]

favorite_categories = {
    "국/공채": [],
    "공공기관": [],
    "보험사": ["현대해상", "농협생명", "메리츠화재", "교보생명", "삼성화재", "삼성생명", "신한라이프", "흥국생명", "동양생명", "미래에셋생명"],
    "5대금융지주": ["신한금융", "하나금융", "KB금융", "농협금융", "우리금융"],
    "5대시중은행": ["농협은행", "국민은행", "신한은행", "우리은행", "하나은행"],
    "카드사": ["KB국민카드", "현대카드", "신한카드", "비씨카드", "삼성카드"],
    "캐피탈": ["한국캐피탈", "현대캐피탈"],
    "지주사": ["SK이노베이션", "GS에너지", "SK", "GS"],
    "에너지": ["SK가스", "GS칼텍스", "S-Oil", "SK에너지", "SK앤무브", "코리아에너지터미널"],
    "발전": ["GS파워", "GSEPS", "삼천리"],
    "자동차": ["LG에너지솔루션", "한온시스템", "포스코퓨처엠", "한국타이어"],
    "전기/전자": ["SK하이닉스", "LG이노텍", "LG전자", "LS일렉트릭"],
    "소비재": ["이마트", "LF", "CJ제일제당", "SK네트웍스", "CJ대한통운"],
    "비철/철강": ["포스코", "현대제철", "고려아연"],
    "석유화학": ["LG화학", "SK지오센트릭"],
    "건설": ["포스코이앤씨"],
    "특수채": ["주택도시보증공사", "기업은행"]
}

if "favorite_keywords" not in st.session_state:
    st.session_state.favorite_keywords = set()
if "search_results" not in st.session_state:
    st.session_state.search_results = {}
if "show_limit" not in st.session_state:
    st.session_state.show_limit = {}
if "search_triggered" not in st.session_state:
    st.session_state.search_triggered = False

for category_keywords in favorite_categories.values():
    st.session_state.favorite_keywords.update(category_keywords)

st.markdown("**즐겨찾기 카테고리 선택**")
cat_col, btn_col = st.columns([5, 1])
with cat_col:
    selected_categories = st.multiselect("카테고리 선택 시 자동으로 즐겨찾기 키워드에 반영됩니다.", list(favorite_categories.keys()))
    for cat in selected_categories:
        st.session_state.favorite_keywords.update(favorite_categories[cat])
with btn_col:
    st.write("")
    category_search_clicked = st.button("🔍 검색", use_container_width=True)

def filter_by_issues(title, desc, selected_keywords, enable_credit_filter, credit_filter_keywords, require_keyword_in_title=False):
    if require_keyword_in_title and selected_keywords:
        if not any(kw.lower() in title.lower() for kw in selected_keywords):
            return False
    if enable_credit_filter and not is_credit_risk_news(title + " " + desc, credit_filter_keywords):
        return False
    return True

def is_credit_risk_news(text, keywords):
    return any(kw in text for kw in keywords)

def fetch_naver_news(query, start_date=None, end_date=None, enable_credit_filter=True, credit_filter_keywords=None, limit=100, require_keyword_in_title=False):
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
            if not filter_by_issues(title, desc, [query], enable_credit_filter, credit_filter_keywords, require_keyword_in_title):
                continue
            articles.append({
                "title": re.sub("<.*?>", "", title),
                "link": item["link"],
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": "Naver"
            })
    return articles[:limit]

def fetch_gnews_news(query, enable_credit_filter=True, credit_filter_keywords=None, limit=100, require_keyword_in_title=False):
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
            if not filter_by_issues(title, desc, [query], enable_credit_filter, credit_filter_keywords, require_keyword_in_title):
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

def is_english(text):
    return all(ord(c) < 128 for c in text if c.isalpha())

def process_keywords(keyword_list, start_date, end_date, enable_credit_filter, credit_filter_keywords, require_keyword_in_title=False):
    for k in keyword_list:
        if is_english(k):
            articles = fetch_gnews_news(k, enable_credit_filter, credit_filter_keywords, require_keyword_in_title=require_keyword_in_title)
        else:
            articles = fetch_naver_news(k, start_date, end_date, enable_credit_filter, credit_filter_keywords, require_keyword_in_title=require_keyword_in_title)
        st.session_state.search_results[k] = articles
        st.session_state.show_limit[k] = 5

def detect_lang_from_title(title):
    return "ko" if re.search(r"[가-힣]", title) else "en"

def summarize_article_from_url(article_url, title):
    try:
        full_text = extract_article_text(article_url)
        if full_text.startswith("본문 추출 오류"):
            return full_text, None
        summary, _ = summarize_with_openai(full_text)
        return summary, full_text
    except Exception as e:
        return f"요약 오류: {e}", None

def render_articles_with_single_summary_and_telegram(results, show_limit):
    all_articles = []
    article_keys = []
    for keyword, articles in results.items():
        for idx, article in enumerate(articles[:show_limit.get(keyword, 5)]):
            all_articles.append(f"[{keyword}] {article['title']} ({article['date']} | {article['source']})")
            article_keys.append((keyword, idx))

    if not all_articles:
        st.info("검색 결과가 없습니다.")
        return

    selected_idx = st.radio("요약/감성분석/텔레그램 전송할 기사를 선택하세요.", range(len(all_articles)), format_func=lambda i: all_articles[i], key="article_selector")
    selected_keyword, selected_article_idx = article_keys[selected_idx]
    selected_article = st.session_state.search_results[selected_keyword][selected_article_idx]

    st.markdown(f"""
    <div style='margin-bottom: 10px; padding: 10px; border: 1px solid #eee; border-radius: 10px; background-color: #fafafa;'>
        <div style='font-weight: bold; font-size: 15px; margin-bottom: 4px;'>
            <a href="{selected_article['link']}" target="_blank" style='text-decoration: none; color: #1155cc;'>
                {selected_article['title']}
            </a>
        </div>
        <div style='font-size: 12px; color: gray;'>
            {selected_article['date']} | {selected_article['source']}
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🔍 선택 기사 요약 및 감성분석"):
        with st.spinner("기사 요약 중..."):
            summary, full_text = summarize_article_from_url(selected_article['link'], selected_article['title'])
            if full_text:
                st.markdown("<div style='font-size:14px; font-weight:bold;'>🔍 본문 요약:</div>", unsafe_allow_html=True)
                st.write(summary)
                sentiment = analyze_sentiment_google(full_text)
                st.markdown(f"<div style='font-size:14px; font-weight:bold;'>🧭 감성 분석: <span style='color:#d60000'>{sentiment}</span></div>", unsafe_allow_html=True)
            else:
                st.warning(summary)

    if st.button("✈️ 선택 기사 텔레그램 전송"):
        try:
            msg = f"*[{selected_article['title']}]({selected_article['link']})*\n{selected_article['date']} | {selected_article['source']}"
            Telegram().send_message(msg)
            st.success("텔레그램으로 전송되었습니다!")
        except Exception as e:
            st.warning(f"텔레그램 전송 오류: {e}")

st.set_page_config(layout="wide")
st.markdown("<h1 style='color:#1a1a1a; margin-bottom:0.5rem;'>📊 Credit Issue Monitoring</h1>", unsafe_allow_html=True)

col1, col2, col3 = st.columns([6, 1, 1])
with col1:
    keywords_input = st.text_input("키워드 (예: 삼성, 한화)", value="", on_change=lambda: st.session_state.__setitem__('search_triggered', True))
with col2:
    st.write("")
    search_clicked = st.button("검색", use_container_width=True)
with col3:
    st.write("")
    fav_add_clicked = st.button("⭐ 즐겨찾기 추가", use_container_width=True)
    if fav_add_clicked:
        new_keywords = {kw.strip() for kw in keywords_input.split(",") if kw.strip()}
        st.session_state.favorite_keywords.update(new_keywords)
        st.success("즐겨찾기에 추가되었습니다.")

date_col1, date_col2 = st.columns([1, 1])
with date_col1:
    start_date = st.date_input("시작일")
with date_col2:
    end_date = st.date_input("종료일")

with st.expander("🛡️ 신용위험 필터 옵션", expanded=True):
    enable_credit_filter = st.checkbox("신용위험 뉴스만 필터링", value=False)
    credit_filter_keywords = st.multiselect(
        "신용위험 관련 키워드 (하나 이상 선택)",
        options=default_credit_issue_patterns,
        default=default_credit_issue_patterns,
        key="credit_filter"
    )

with st.expander("🔍 키워드 필터 옵션", expanded=True):
    require_keyword_in_title = st.checkbox("기사 제목에 키워드가 포함된 경우만 보기", value=True)

fav_col1, fav_col2 = st.columns([5, 1])
with fav_col1:
    fav_selected = st.multiselect("⭐ 즐겨찾기에서 검색", sorted(st.session_state.favorite_keywords))
with fav_col2:
    st.write("")
    fav_search_clicked = st.button("즐겨찾기로 검색", use_container_width=True)

search_clicked = False

if keywords_input:
    keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()]
    if len(keyword_list) > 10:
        st.warning("키워드는 최대 10개까지 입력 가능합니다.")
    else:
        search_clicked = True

if search_clicked or st.session_state.get("search_triggered"):
    keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()]
    if len(keyword_list) > 10:
        st.warning("키워드는 최대 10개까지 입력 가능합니다.")
    else:
        with st.spinner("뉴스 검색 중..."):
            process_keywords(keyword_list, start_date, end_date, enable_credit_filter, credit_filter_keywords)
    st.session_state.search_triggered = False

if fav_search_clicked and fav_selected:
    with st.spinner("뉴스 검색 중..."):
        process_keywords(fav_selected, start_date, end_date, enable_credit_filter, credit_filter_keywords)

if category_search_clicked and selected_categories:
    with st.spinner("뉴스 검색 중..."):
        keywords = set()
        for cat in selected_categories:
            keywords.update(favorite_categories[cat])
        process_keywords(
            sorted(keywords),
            start_date,
            end_date,
            enable_credit_filter,
            credit_filter_keywords,
            require_keyword_in_title
        )

if st.session_state.search_results:
    render_articles_with_single_summary_and_telegram(st.session_state.search_results, st.session_state.show_limit)
