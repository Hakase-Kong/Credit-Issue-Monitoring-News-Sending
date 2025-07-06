import nltk
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
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

# --- 세션 상태 변수 초기화 (항상 위젯보다 먼저!) ---
if "favorite_keywords" not in st.session_state:
    st.session_state.favorite_keywords = set()
if "search_results" not in st.session_state:
    st.session_state.search_results = {}
if "show_limit" not in st.session_state:
    st.session_state.show_limit = {}
if "search_triggered" not in st.session_state:
    st.session_state.search_triggered = False

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def detect_lang(text):
    return "ko" if re.search(r"[가-힣]", text) else "en"

def extract_article_text(url):
    try:
        article = newspaper.article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        return f"본문 추출 오류: {e}"

def summarize_and_sentiment_with_openai(text):
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", None, None, None
    lang = detect_lang(text)
    if lang == "ko":
        prompt = (
            "아래 기사 본문을 요약하고 감성분석을 해줘.\n\n"
            "- [한 줄 요약]: 기사 전체 내용을 한 문장으로 요약\n"
            "- [요약본]: 기사 내용을 2~3 문단(각 문단 2~4문장)으로, 핵심 내용을 충분히 파악할 수 있게 요약\n"
            "- [감성]: 기사 전체의 감정을 긍정/부정/중립 중 하나로만 답해줘. "
            "만약 파산, 자금난, 회생, 적자, 구조조정, 영업손실, 부도, 채무불이행, 경영 위기 등 부정적 사건이 중심이면 반드시 '부정'으로 답해줘.\n"
            "광고, 배너, 추천기사, 서비스 안내 등 기사 본문과 무관한 내용은 모두 요약과 감성분석에서 제외.\n\n"
            "아래 포맷으로 답변해줘:\n"
            "[한 줄 요약]: (여기에 한 줄 요약)\n"
            "[요약본]: (여기에 여러 문단 요약)\n"
            "[감성]: (긍정/부정/중립 중 하나만)\n\n"
            "[기사 본문]\n" + text
        )
    else:
        prompt = (
            "Summarize the following news article and analyze its sentiment.\n\n"
            "- [One-line Summary]: Summarize the entire article in one sentence.\n"
            "- [Summary]: Summarize the article in 2–3 paragraphs (each 2–4 sentences), so that the main content is well understood.\n"
            "- [Sentiment]: Classify the overall sentiment as one of: positive, negative, or neutral. "
            "If the article centers on bankruptcy, financial distress, restructuring, insolvency, operating loss, default, or management crisis, you must answer 'negative'.\n"
            "Exclude any advertisements, banners, recommended articles, or unrelated content.\n\n"
            "Respond in this format:\n"
            "[One-line Summary]: (your one-line summary)\n"
            "[Summary]: (your multi-paragraph summary)\n"
            "[Sentiment]: (positive/negative/neutral only)\n\n"
            "[ARTICLE]\n" + text
        )
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": prompt}
        ],
        max_tokens=1024,
        temperature=0.3
    )
    answer = response.choices[0].message.content.strip()
    if lang == "ko":
        m1 = re.search(r"\[한 줄 요약\]:\s*(.+)", answer)
        m2 = re.search(r"\[요약본\]:\s*([\s\S]+?)(?:\[감성\]:|$)", answer)
        m3 = re.search(r"\[감성\]:\s*(.+)", answer)
    else:
        m1 = re.search(r"\[One-line Summary\]:\s*(.+)", answer)
        m2 = re.search(r"\[Summary\]:\s*([\s\S]+?)(?:\[Sentiment\]:|$)", answer)
        m3 = re.search(r"\[Sentiment\]:\s*(.+)", answer)
    one_line = m1.group(1).strip() if m1 else ""
    summary = m2.group(1).strip() if m2 else answer
    sentiment = m3.group(1).strip() if m3 else ""
    return one_line, summary, sentiment, text

# --- 대분류(산업) & 소분류(필터 키워드) 구조 ---
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
major_categories = list(favorite_categories.keys())
sub_categories = {cat: favorite_categories[cat] for cat in major_categories}

# --- UI: 키워드 입력창 ---
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

# --- 즐겨찾기 카테고리 선택 (키워드 입력창 바로 아래) ---
st.markdown("**즐겨찾기 카테고리 선택**")
cat_col, btn_col = st.columns([5, 1])
with cat_col:
    selected_categories = st.multiselect("카테고리 선택 시 자동으로 즐겨찾기 키워드에 반영됩니다.", major_categories)
    for cat in selected_categories:
        st.session_state.favorite_keywords.update(favorite_categories[cat])
with btn_col:
    st.write("")
    category_search_clicked = st.button("🔍 검색", use_container_width=True)

# --- 즐겨찾기에서 검색 (즐겨찾기 카테고리 선택 바로 아래) ---
fav_col1, fav_col2 = st.columns([5, 1])
with fav_col1:
    fav_selected = st.multiselect("⭐ 즐겨찾기에서 검색", sorted(st.session_state.favorite_keywords))
with fav_col2:
    st.write("")
    fav_search_clicked = st.button("즐겨찾기로 검색", use_container_width=True)

# --- 날짜 입력 ---
date_col1, date_col2 = st.columns([1, 1])
with date_col1:
    start_date = st.date_input("시작일")
with date_col2:
    end_date = st.date_input("종료일")

# --- 신용위험 필터 옵션 ---
with st.expander("🛡️ 신용위험 필터 옵션", expanded=True):
    credit_keywords = [
        "신용등급", "신용평가", "하향", "상향", "강등", "조정", "부도",
        "파산", "디폴트", "채무불이행", "적자", "영업손실", "현금흐름", "자금난",
        "재무위험", "부정적 전망", "긍정적 전망", "기업회생", "워크아웃", "구조조정", "자본잠식"
    ]
    credit_filter_keywords = st.multiselect(
        "신용위험 관련 키워드 (하나 이상 선택)",
        options=credit_keywords,
        default=credit_keywords,
        key="credit_filter"
    )

# --- 키워드 필터 옵션 (기본 해제) ---
with st.expander("🔍 키워드 필터 옵션", expanded=True):
    require_keyword_in_title = st.checkbox("기사 제목에 키워드가 포함된 경우만 보기", value=False)

# --- 산업별 필터 옵션 (박스형태, 한 줄에 배치, 태그 UI) ---
with st.expander("🏭 산업별 필터 옵션", expanded=True):
    col_major, col_sub = st.columns([1, 2])
    with col_major:
        selected_major = st.selectbox("대분류(산업)", major_categories, key="industry_major")
    with col_sub:
        selected_sub = st.multiselect(
            "소분류(필터 키워드)",
            sub_categories[selected_major],
            key="industry_sub"
        )

# --- 재무위험 필터 옵션 ---
with st.expander("💰 재무위험 필터 옵션", expanded=True):
    finance_keywords = ["자산", "총자산", "부채", "자본", "매출", "비용", "영업이익", "순이익"]
    finance_filter_keywords = st.multiselect(
        "재무위험 관련 키워드",
        options=finance_keywords,
        default=[],
        key="finance_filter"
    )

# --- 법/정책 위험 필터 옵션 ---
with st.expander("⚖️ 법/정책 위험 필터 옵션", expanded=True):
    law_keywords = ["테스트1", "테스트2", "테스트3"]
    law_filter_keywords = st.multiselect(
        "법/정책 위험 관련 키워드",
        options=law_keywords,
        default=[],
        key="law_filter"
    )

# --- CSS: 붉은색 태그 스타일 ---
st.markdown("""
<style>
.stMultiSelect [data-baseweb="tag"] {
    background-color: #ff5c5c !important;
    color: white !important;
    border: none !important;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# --- OR 조건 필터링 함수 ---
def or_keyword_filter(article, *keyword_lists):
    text = (article.get("title", "") + " " + article.get("description", "") + " " + article.get("full_text", ""))
    for keywords in keyword_lists:
        if any(kw in text for kw in keywords if kw):
            return True
    return False

# --- 텔레그램 클래스 ---
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

# --- 뉴스 API 함수 (네이버/GNews) ---
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
            return full_text, None, None, None
        one_line, summary, sentiment, _ = summarize_and_sentiment_with_openai(full_text)
        return one_line, summary, sentiment, full_text
    except Exception as e:
        return f"요약 오류: {e}", None, None, None

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
        with st.spinner("기사 요약 및 감성분석 중..."):
            one_line, summary, sentiment, full_text = summarize_article_from_url(selected_article['link'], selected_article['title'])
            if full_text:
                st.markdown("**[한 줄 요약]**")
                st.write(one_line)
                st.markdown("**[요약본]**")
                st.write(summary)
                st.markdown(f"**[감성 분석]**: :red[{sentiment}]")
            else:
                st.warning(one_line)

    if st.button("✈️ 선택 기사 텔레그램 전송"):
        try:
            msg = f"*[{selected_article['title']}]({selected_article['link']})*\n{selected_article['date']} | {selected_article['source']}"
            Telegram().send_message(msg)
            st.success("텔레그램으로 전송되었습니다!")
        except Exception as e:
            st.warning(f"텔레그램 전송 오류: {e}")

# --- 실제 뉴스 검색/필터링/요약/감성분석 실행 ---
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
            process_keywords(keyword_list, start_date, end_date, True, credit_filter_keywords, require_keyword_in_title)
    st.session_state.search_triggered = False

if fav_search_clicked and fav_selected:
    with st.spinner("뉴스 검색 중..."):
        process_keywords(fav_selected, start_date, end_date, True, credit_filter_keywords, require_keyword_in_title)

if category_search_clicked and selected_categories:
    with st.spinner("뉴스 검색 중..."):
        keywords = set()
        for cat in selected_categories:
            keywords.update(favorite_categories[cat])
        process_keywords(
            sorted(keywords),
            start_date,
            end_date,
            True,
            credit_filter_keywords,
            require_keyword_in_title
        )

# --- 필터링: OR 조건(모든 필터 옵션 합산) ---
def article_passes_all_filters(article):
    return or_keyword_filter(
        article,
        credit_filter_keywords,
        selected_sub,
        finance_filter_keywords,
        law_filter_keywords
    )

if st.session_state.search_results:
    # OR 조건 필터링 적용
    filtered_results = {}
    for keyword, articles in st.session_state.search_results.items():
        filtered_articles = [a for a in articles if article_passes_all_filters(a)]
        if filtered_articles:
            filtered_results[keyword] = filtered_articles
    render_articles_with_single_summary_and_telegram(filtered_results, st.session_state.show_limit)
