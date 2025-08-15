import os
import streamlit as st
import pandas as pd
from io import BytesIO
import requests
import re
from datetime import datetime, timedelta
import telepot
from openai import OpenAI
import newspaper
import difflib
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import html
import json
from datetime import datetime, timedelta

# --- config.json 로드 ---
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

EXCLUDE_TITLE_KEYWORDS = config["EXCLUDE_TITLE_KEYWORDS"] # --- 제외 키워드 ---
ALLOWED_SOURCES = set(config["ALLOWED_SOURCES"]) # 필터링할 언론사 도메인 리스트 (www. 제거된 도메인 기준)
favorite_categories = config["favorite_categories"] # --- 즐겨찾기 카테고리(변경 금지) ---
excel_company_categories = config["excel_company_categories"]
common_filter_categories = config["common_filter_categories"] # --- 공통 필터 옵션(대분류/소분류 없이 모두 적용) ---
industry_filter_categories = config["industry_filter_categories"] # --- 산업별 필터 옵션 ---
SYNONYM_MAP = config["synonym_map"]

# 공통 필터 키워드 전체 리스트 생성
ALL_COMMON_FILTER_KEYWORDS = []
for keywords in common_filter_categories.values():
    ALL_COMMON_FILTER_KEYWORDS.extend(keywords)

def expand_keywords_with_synonyms(original_keywords):
    expanded_map = {}
    for kw in original_keywords:
        synonyms = SYNONYM_MAP.get(kw, [])
        expanded_map[kw] = [kw] + synonyms
    return expanded_map

def process_keywords_with_synonyms(favorite_to_expand_map, start_date, end_date, require_keyword_in_title=False):
    for main_kw, kw_list in favorite_to_expand_map.items():
        all_articles = []
        for search_kw in kw_list:
            fetched = fetch_naver_news(search_kw, start_date, end_date,
                                       require_keyword_in_title=require_keyword_in_title)
            all_articles.extend(fetched)

        # 중복 제거
        if st.session_state.get("remove_duplicate_articles", False):
            all_articles = remove_duplicates(all_articles)

        st.session_state.search_results[main_kw] = all_articles
        if main_kw not in st.session_state.show_limit:
            st.session_state.show_limit[main_kw] = 5

# --- CSS 스타일 ---
st.markdown("""
<style>
[data-testid="column"] > div { gap: 0rem !important; }
.stMultiSelect [data-baseweb="tag"] { background-color: #ff5c5c !important; color: white !important; border: none !important; font-weight: bold; }
.sentiment-badge { display: inline-block; padding: 0.08em 0.6em; margin-left: 0.2em; border-radius: 0.8em; font-size: 0.85em; font-weight: bold; vertical-align: middle; }
.sentiment-positive { background: #2ecc40; color: #fff; }
.sentiment-negative { background: #ff4136; color: #fff; }
.stBox { background: #fcfcfc; border-radius: 0.7em; border: 1.5px solid #e0e2e6; margin-bottom: 1.2em; padding: 1.1em 1.2em 1.2em 1.2em; box-shadow: 0 2px 8px 0 rgba(0,0,0,0.03); }
.flex-row-bottom { display: flex; align-items: flex-end; gap: 0.5rem; margin-bottom: 0.5rem; }
.flex-grow { flex: 1 1 0%; }
.flex-btn { min-width: 90px; }
</style>

""", unsafe_allow_html=True)
st.markdown("""
<style>
.news-title { 
    word-break: break-all !important; 
    white-space: normal !important; 
    display: block !important;
    overflow: visible !important;
}
</style>
""", unsafe_allow_html=True)

def exclude_by_title_keywords(title, exclude_keywords):
    for word in exclude_keywords:
        if word in title:
            return True
    return False

def init_session_state():
    """Streamlit 세션 변수들을 일괄 초기화"""
    defaults = {
        "favorite_keywords": set(),
        "search_results": {},
        "show_limit": {},
        "search_triggered": False,
        "selected_articles": [],
        "cat_multi": [],
        "cat_major_autoset": [],
        "important_articles_preview": [],
        "important_selected_index": [],
        "article_checked_left": {},
        "article_checked": {},
        "industry_major_sub_map": {},
        "end_date": datetime.today().date(),
        "start_date": datetime.today().date() - timedelta(days=7),
        "remove_duplicate_articles": True,
        "require_exact_keyword_in_title_or_content": True,
        "filter_allowed_sources_only": False,
        "use_industry_filter": True,
        "show_sentiment_badge": False,
        "enable_summary": True,
        "keyword_input": ""
    }
    for key, default_val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_val

# --- UI 시작 ---
st.set_page_config(layout="wide")

# ✅ 세션 변수 초기화 호출
init_session_state()

col_title, col_option1, col_option2 = st.columns([0.5, 0.2, 0.3])

# --- 카테고리-산업 대분류 매핑 함수 ---
def get_industry_majors_from_favorites(selected_categories):
    favorite_to_industry_major = config["favorite_to_industry_major"]
    majors = set()
    for cat in selected_categories:
        for major in favorite_to_industry_major.get(cat, []):
            majors.add(major)
    return list(majors)

# --- UI 시작 ---
st.set_page_config(layout="wide")
col_title, col_option1, col_option2 = st.columns([0.5, 0.2, 0.3])
with col_title:
    st.markdown(
        "<h1 style='color:#1a1a1a; margin-bottom:0.5rem;'>"
        "<a href='https://credit-issue-monitoring-news-sending.onrender.com/' target='_blank' style='text-decoration:none; color:#1a1a1a;'>"
        "📊 Credit Issue Monitoring</a></h1>",
        unsafe_allow_html=True
    )
with col_option1:
    show_sentiment_badge = st.checkbox("감성분석 배지표시", key="show_sentiment_badge")
with col_option2:
    enable_summary = st.checkbox("요약 기능", key="enable_summary")
    
col_kw_input, col_kw_btn = st.columns([0.8, 0.2])
with col_kw_input:
    keywords_input = st.text_input(label="", value="", key="keyword_input", label_visibility="collapsed")
with col_kw_btn:
    search_clicked = st.button("검색", key="search_btn", help="키워드로 검색", use_container_width=True)

st.markdown("**⭐ 산업군 선택**")
col_cat_input, col_cat_btn = st.columns([0.8, 0.2])
with col_cat_input:
    selected_categories = st.multiselect(
        "",
        list(favorite_categories.keys()), key="cat_multi", label_visibility="collapsed"
        )
if selected_categories:
    auto_selected_majors = get_industry_majors_from_favorites(selected_categories)
    st.session_state.cat_major_autoset = auto_selected_majors.copy()
else:
    st.session_state.cat_major_autoset = []
with col_cat_btn:
    category_search_clicked = st.button("🔍 검색", key="cat_search_btn", help="카테고리로 검색", use_container_width=True)
for cat in selected_categories:
    st.session_state.favorite_keywords.update(favorite_categories[cat])

# 날짜 입력 (기본 세팅: 종료일=오늘, 시작일=오늘-7일)
date_col1, date_col2 = st.columns([1, 1])
with date_col1:
    start_date = st.date_input("시작일", value=st.session_state["start_date"], key="start_date_input")
    st.session_state["start_date"] = start_date
with date_col2:
    end_date = st.date_input("종료일", value=st.session_state["end_date"], key="end_date_input")
    st.session_state["end_date"] = end_date

with st.expander("🧩 공통 필터 옵션 (항상 적용됨)"):
    for major, subs in common_filter_categories.items():
        st.markdown(f"**{major}**: {', '.join(subs)}")

with st.expander("🏭 산업별 필터 옵션 (대분류별 소분류 필터링)"):
    use_industry_filter = st.checkbox("이 필터 적용", key="use_industry_filter")

    # UI: 선택된 산업군에서 자동 매핑된 대분류 추출
    selected_major_map = get_industry_majors_from_favorites(selected_categories)

    updated_map = {}
    for major in selected_major_map:
        options = industry_filter_categories.get(major, [])
        default_selected = options if major not in st.session_state.industry_major_sub_map else st.session_state.industry_major_sub_map[major]
        selected_sub = st.multiselect(
            f"{major} 소분류 키워드",
            options,
            default=default_selected,
            key=f"subfilter_{major}"
        )
        updated_map[major] = selected_sub

    st.session_state.industry_major_sub_map = updated_map
    
# --- 중복 기사 제거 기능 체크박스 포함된 키워드 필터 옵션 ---
with st.expander("🔍 키워드 필터 옵션"):
    require_exact_keyword_in_title_or_content = st.checkbox("키워드가 제목 또는 본문에 포함된 기사만 보기", key="require_exact_keyword_in_title_or_content")
    remove_duplicate_articles = st.checkbox("중복 기사 제거", key="remove_duplicate_articles", help="키워드 검색 후 중복 기사를 제거합니다.")
    filter_allowed_sources_only = st.checkbox(
        "특정 언론사만 검색", 
        key="filter_allowed_sources_only", 
        help="선택된 메이저 언론사만 필터링하고, 그 외 언론은 제외합니다."
    )

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def detect_lang(text):
    return "ko" if re.search(r"[가-힣]", text) else "en"

def summarize_and_sentiment_with_openai(text, do_summary=True):
    """
    본문 요약/감성분석과 모든 예외 처리 포함.
    - OpenAI 응답 포맷 불일치/비정규 출력/본문 오류/키 누락 모두 안전하게 처리
    - 반환값: (한줄요약, 주요키워드, 감성, 전체본문)
    """
    # 1. 키 없을 때 반환
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", "", "감성 추출 실패", text

    # 2. 본문 체크
    if not text or "본문 추출 오류" in text:
        return "기사 본문이 추출 실패", "", "감성 추출 실패", text

    # 3. 프롬프트(최적화 version, 이전 답변 참고)
    lang = detect_lang(text)
    if lang == "ko":
        role_prompt = (
            "너는 경제 뉴스 요약/분석 전문가야. 한 문장 요약에는 반드시 주체, 핵심 사건, 결과를, "
            "감성 분류는 파산·감원 등 부정 이슈면 '부정', 신규 투자·호재 땐 '긍정', 중립은 금지. 포맷은 지정된 키 그대로."
        )
        main_prompt = """
아래 기사 본문을 분석해 다음 세가지를 정확히 응답하라.

[한 줄 요약]: 주요 인물/기업, 사건, 결과 포함
[검색 키워드]: 이 기사가 검색에 사용된 키워드를 콤마(,)로 모두 명시 
[감성]: 긍정 또는 부정 (둘 중 하나만)
[주요 키워드]: 인물, 기업, 조직명만 콤마(,)로, 없으면 없음

[기사 본문]
""" + text
    else:
        role_prompt = (
            "You are a financial news summarization expert. The summary must contain entity, main event, outcome. "
            "Sentiment is only positive/negative strictly (never neutral). Use labels exactly as requested."
        )
        main_prompt = """
Analyze the article and extract these three exactly:

[One-line Summary]: One sentence, include entity, event, outcome
[Search Keywords]: Comma-separated list of keywords used to retrieve this article  
[Sentiment]: positive or negative (only one)
[Key Entities]: All mentioned companies/people/org, comma separated, or None

[ARTICLE]
""" + text

    # 4. OpenAI 호출 & 오류 처리
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": role_prompt},
                {"role": "user", "content": main_prompt}
            ],
            max_tokens=900,
            temperature=0.3
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 오류: {e}", "", "감성 추출 실패", text

    # 5. 정규식 파싱 (실패시에도 기본값 반환, None 방지)
    if lang == "ko":
        m1 = re.search(r"\[한 줄 요약\]:\s*([^\n]+)", answer)
        m2 = re.search(r"\[주요 키워드\]:\s*([^\n]+)", answer)
        m3 = re.search(r"\[감성\]:\s*(긍정|부정)", answer)
        # 추가: 만일 '감성'이 뒤에 나오면
        if not m3:
            m3 = re.search(r"\[감성\]:\s*([^\n]+)", answer)
    else:
        m1 = re.search(r"\[One-line Summary\]:\s*([^\n]+)", answer)
        m2 = re.search(r"\[Key Entities\]:\s*([^\n]+)", answer)
        m3 = re.search(r"\[Sentiment\]:\s*(positive|negative)", answer, re.I)
        # fallback for Sentiment
        if not m3:
            m3 = re.search(r"\[Sentiment\]:\s*([^\n]+)", answer)

    # 6. 값 추출 & 최종 보정
    one_line = m1.group(1).strip() if (m1 and do_summary) else "요약 추출 실패"
    keywords = m2.group(1).strip() if m2 else ""
    sentiment = ''
    if m3:
        sentiment = m3.group(1).strip()
        # 영문 응답을 한글로 통일
        if sentiment.lower() == 'positive':
            sentiment = '긍정'
        elif sentiment.lower() == 'negative':
            sentiment = '부정'
        elif sentiment not in ['긍정', '부정']:
            sentiment = '감성 추출 실패'
    else:
        sentiment = '감성 추출 실패'

    # 7. 누락, 빈값 보정 (오류 메시지 반환 절대 방지)
    if not one_line or one_line.lower() in ["none", ""]:
        one_line = "요약 추출 실패"
    if not sentiment or sentiment.lower() in ["none", "중립", "neutral", ""]:
        sentiment = "감성 추출 실패"
    if not keywords or keywords.lower() in ["none", "없음"]:
        keywords = ""

    return one_line, keywords, sentiment, text

def infer_source_from_url(url):
    domain = urlparse(url).netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

class Telegram:
    def __init__(self):
        self.bot = telepot.Bot(TELEGRAM_TOKEN)  # 이미 환경변수 기반
        self.chat_id = TELEGRAM_CHAT_ID
    def send_message(self, message):
        self.bot.sendMessage(self.chat_id, message, parse_mode="Markdown", disable_web_page_preview=True)
        
def filter_by_issues(title, desc, selected_keywords, require_keyword_in_title=False):
    if require_keyword_in_title and selected_keywords:
        if not any(kw.lower() in title.lower() for kw in selected_keywords):
            return False
    return True

def fetch_naver_news(query, start_date=None, end_date=None, limit=1000, require_keyword_in_title=False):
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    articles = []
    for start in range(1, 1001, 100):
        if len(articles) >= limit:
            break
        params = {
            "query": query,
            "display": 100,
            "start": start,
            "sort": "date"
        }
        response = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, params=params)
        if response.status_code != 200:
            break

        items = response.json().get("items", [])
        for item in items:
            title = html.unescape(re.sub("<.*?>", "", item["title"]))
            desc  = html.unescape(re.sub("<.*?>", "", item["description"]))
            pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z").date()

            if start_date and pub_date < start_date:
                continue
            if end_date and pub_date > end_date:
                continue
            if not filter_by_issues(title, desc, [query], require_keyword_in_title):
                continue
            if exclude_by_title_keywords(title, EXCLUDE_TITLE_KEYWORDS):
                continue

            source = item.get("source") or infer_source_from_url(item.get("originallink", "")) or "Naver"
            source_domain = source.lower()
            if source_domain.startswith("www."):
                source_domain = source_domain[4:]

            real_link = item.get("originallink") or item["link"]

            articles.append({
                "title": title,
                "description": desc,  # 혹시 엑셀에 설명도 쓸 경우 대비
                "link": real_link,
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": source_domain
            })

        if len(items) < 100:
            break
    return articles[:limit]

def process_keywords(keyword_list, start_date, end_date, require_keyword_in_title=False):
    for k in keyword_list:
        articles = fetch_naver_news(k, start_date, end_date, require_keyword_in_title=require_keyword_in_title)
        st.session_state.search_results[k] = articles
        if k not in st.session_state.show_limit:
            st.session_state.show_limit[k] = 5

def summarize_article_from_url(article_url, title, do_summary=True):
    cache_key_base = re.sub(r"\W+", "", article_url)[-16:]
    summary_key = f"summary_{cache_key_base}"

    # 이미 요약 결과가 있으면 그대로 반환
    if summary_key in st.session_state:
        return st.session_state[summary_key]

    try:
        full_text = extract_article_text(article_url)
        if full_text.startswith("본문 추출 오류"):
            result = (full_text, None, None, None)
        else:
            one_line, summary, sentiment, _ = summarize_and_sentiment_with_openai(full_text, do_summary=do_summary)
            result = (one_line, summary, sentiment, full_text)
    except Exception as e:
        result = (f"요약 오류: {e}", None, None, None)

    # 캐시에 저장
    st.session_state[summary_key] = result
    return result

def or_keyword_filter(article, *keyword_lists):
    text = (article.get("title", "") + " " + article.get("description", "") + " " + article.get("full_text", ""))
    for keywords in keyword_lists:
        if any(kw in text for kw in keywords if kw):
            return True
    return False

def article_contains_exact_keyword(article, keywords):
    title = article.get("title", "")
    content = ""
    cache_key = article.get("link", "")
    summary_cache_key = None
    for key in st.session_state.keys():
        if key.startswith("summary_") and cache_key in key:
            summary_cache_key = key
            break
    if summary_cache_key and isinstance(st.session_state[summary_cache_key], tuple):
        _, _, _, content = st.session_state[summary_cache_key]
    for kw in keywords:
        if kw and (kw in title or (content and kw in content)):
            return True
    return False

def article_passes_all_filters(article):
    # 제목에 제외 키워드가 포함되면 제외
    if exclude_by_title_keywords(article.get('title', ''), EXCLUDE_TITLE_KEYWORDS):
        return False

    # 날짜 범위 필터링
    try:
        pub_date = datetime.strptime(article['date'], '%Y-%m-%d').date()
        if pub_date < st.session_state.get("start_date") or pub_date > st.session_state.get("end_date"):
            return False
    except:
        return False

    # 키워드 필터: 입력 키워드 및 카테고리 키워드 집합 준비
    all_keywords = []
    if "keyword_input" in st.session_state:
        all_keywords.extend([k.strip() for k in st.session_state["keyword_input"].split(",") if k.strip()])
    if "cat_multi" in st.session_state:
        for cat in st.session_state["cat_multi"]:
            all_keywords.extend(favorite_categories.get(cat, []))

    # 키워드 필터(입력 및 카테고리 키워드) 통과 여부
    keyword_passed = article_contains_exact_keyword(article, all_keywords)

    # 언론사 도메인 필터링 (특정 언론사만 필터링)
    if st.session_state.get("filter_allowed_sources_only", True):
        source = article.get('source', '').lower()
        if source.startswith("www."):
            source = source[4:]
        if source not in ALLOWED_SOURCES:
            return False

    # 공통 필터 조건 (AND 조건, 즉 반드시 통과해야 함)
    common_passed = or_keyword_filter(article, ALL_COMMON_FILTER_KEYWORDS)
    if not common_passed:
        return False

    # 산업별 필터 조건 (OR 조건)
    industry_passed = True
    if st.session_state.get("use_industry_filter", False):
        keyword = article.get("키워드")  # 회사명 또는 키워드 항목명
        matched_major = None
        for cat, companies in favorite_categories.items():
            if keyword in companies:
                majors = get_industry_majors_from_favorites([cat])
                if majors:
                    matched_major = majors[0]
                    break
        if matched_major:
            sub_keyword_filter = st.session_state.industry_major_sub_map.get(matched_major, [])
            if sub_keyword_filter:
                industry_passed = or_keyword_filter(article, sub_keyword_filter)

    # 최종 필터링: 공통 필터는 반드시 통과하고,
    # 산업별 필터나 키워드 필터 중 하나라도 통과하면 통과
    if not (industry_passed or keyword_passed):
        return False

    return True

# --- 중복 기사 제거 함수 ---
def is_similar(title1, title2, threshold=0.5):
    ratio = difflib.SequenceMatcher(None, title1, title2).ratio()
    return ratio >= threshold

def remove_duplicates(articles):
    unique_articles = []
    titles = []
    for article in articles:
        title = article.get("title", "")
        if all(not is_similar(title, existing_title) for existing_title in titles):
            unique_articles.append(article)
            titles.append(title)
    return unique_articles

# 항상 먼저 선언해 에러 방지
keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()] if keywords_input else []
search_clicked = False

if keyword_list:
        search_clicked = True

if keyword_list and (search_clicked or st.session_state.get("search_triggered")):
    with st.spinner("뉴스 검색 중..."):
        # 동의어 확장
        expanded = expand_keywords_with_synonyms(sorted(keyword_list))
        process_keywords_with_synonyms(
            expanded,
            st.session_state["start_date"],
            st.session_state["end_date"],
            require_keyword_in_title=st.session_state.get("require_exact_keyword_in_title_or_content", False)
        )
    st.session_state.search_triggered = False


if category_search_clicked and selected_categories:
    with st.spinner("뉴스 검색 중..."):
        keywords = set()
        for cat in selected_categories:
            keywords.update(favorite_categories[cat])

        expanded = expand_keywords_with_synonyms(sorted(keywords))
        process_keywords_with_synonyms(
            expanded,
            st.session_state["start_date"],
            st.session_state["end_date"],
            require_keyword_in_title=st.session_state.get("require_exact_keyword_in_title_or_content", False)
        )

def safe_title(val):
    if pd.isnull(val) or str(val).strip() == "" or str(val).lower() == "nan" or str(val) == "0":
        return "제목없음"
    return str(val)

def clean_excel_formula_text(text):
    """엑셀 수식(HYPERLINK)에서 깨짐 방지용 전처리"""
    if not isinstance(text, str):  # None이나 숫자이면 문자 변환
        text = str(text)
    text = text.replace('"', "'")   # 큰따옴표 → 홑따옴표
    text = text.replace('\n', ' ')  # 줄바꿈 → 공백
    text = text.replace('\r', '')
    return text[:250]  # 안전하게 255자 미만으로 제한

def get_excel_download_with_favorite_and_excel_company_col(summary_data, favorite_categories, excel_company_categories, search_results):
    company_order = []
    for cat in [
        "국/공채", "공공기관", "보험사", "5대금융지주", "5대시중은행", "카드사", "캐피탈",
        "지주사", "에너지", "발전", "자동차", "전기/전자", "소비재", "비철/철강", "석유화학", "건설", "특수채"
    ]:
        company_order.extend(favorite_categories.get(cat, []))
    excel_company_order = []
    for cat in [
        "국/공채", "공공기관", "보험사", "5대금융지주", "5대시중은행", "카드사", "캐피탈",
        "지주사", "에너지", "발전", "자동차", "전기/전자", "소비재", "비철/철강", "석유화학", "건설", "특수채"
    ]:
        excel_company_order.extend(excel_company_categories.get(cat, []))

    df_articles = pd.DataFrame(summary_data)

    if "키워드" not in df_articles.columns:
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            pd.DataFrame(columns=["기업명", "표기명", "건수", "긍정 뉴스", "부정 뉴스"]).to_excel(writer, index=False)
        output.seek(0)
        return output

    result_rows = []
    for idx, company in enumerate(company_order):
        excel_company_name = excel_company_order[idx] if idx < len(excel_company_order) else ""

        filtered_articles = [
            a for a in search_results.get(company, [])
            if article_passes_all_filters(a)
        ]
        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)
        total_count = len(filtered_articles)

        comp_articles = df_articles[df_articles["키워드"] == company]
        pos_news = comp_articles[comp_articles["감성"] == "긍정"].sort_values(by="날짜", ascending=False)
        neg_news = comp_articles[comp_articles["감성"] == "부정"].sort_values(by="날짜", ascending=False)

        if not pos_news.empty:
            pos_date = clean_excel_formula_text(pos_news.iloc[0]["날짜"])
            pos_title = clean_excel_formula_text(pos_news.iloc[0]["기사제목"])
            pos_link = clean_excel_formula_text(pos_news.iloc[0]["링크"])
            pos_display = f'({pos_date}) {pos_title}'
            pos_hyperlink = f'=HYPERLINK("{pos_link}", "{pos_display}")'
        else:
            pos_hyperlink = ""

        if not neg_news.empty:
            neg_date = clean_excel_formula_text(neg_news.iloc[0]["날짜"])
            neg_title = clean_excel_formula_text(neg_news.iloc[0]["기사제목"])
            neg_link = clean_excel_formula_text(neg_news.iloc[0]["링크"])
            neg_display = f'({neg_date}) {neg_title}'
            neg_hyperlink = f'=HYPERLINK("{neg_link}", "{neg_display}")'
        else:
            neg_hyperlink = ""

        result_rows.append({
            "기업명": company,
            "표기명": excel_company_name,
            "건수": total_count,
            "긍정 뉴스": pos_hyperlink,
            "부정 뉴스": neg_hyperlink
        })

    df_result = pd.DataFrame(result_rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_result.to_excel(writer, index=False, sheet_name='뉴스요약')
    output.seek(0)
    return output

def generate_important_article_list(search_results, common_keywords, industry_keywords, favorites):
    import os
    from openai import OpenAI
    import re

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
    result = []

    for category, companies in favorites.items():
        for comp in companies:
            articles = search_results.get(comp, [])
            filtered_keywords = list(set(common_keywords + industry_keywords))
            target_articles = [a for a in articles if any(kw in a["title"] for kw in filtered_keywords)]

            if not target_articles:
                continue

            prompt_list = "\n".join([f"{i+1}. {a['title']} - {a['link']}" for i, a in enumerate(target_articles)])

            # ◾️ 여기에 강화된 프롬프트 적용
            prompt = (
                f"[기사 목록]\n{prompt_list}\n\n"
                "1. 각 기사 내용을 읽고, 기사의 전반적인 감성 톤(긍정/부정)을 판단해 주세요.\n"
                "   - 만약 긍정과 부정이 혼재된 경우, 기사의 전체적인 분위기에서 우세한 감성 톤을 기준으로 판단합니다.\n\n"
                "2. 기사에 언급된 기업의 채권 투자자 입장에서 판단해,\n"
                "   2.1 재무 안정성, 현금창출력, 실적 개선, 리스크 완화 여부, 긍정적 전망에 긍정적으로 기여하는 핵심 긍정 기사 1건\n"
                "   2.2 수익성 저하, 리스크 확대, 부정적 전망에 해당하는 핵심 부정 기사 1건을 각각 선정해주세요.\n"
                "3. 감성 분류(긍정/부정)에 해당하는 기사가 없으면 공란으로 남겨주세요.\n\n"
                "[긍정]: (긍정적 선정 기사 제목)\n[부정]: (부정적 선정 기사 제목)"
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0.3
                )
                answer = response.choices[0].message.content.strip()
                pos_title = re.search(r"\[긍정\]:\s*(.+)", answer)
                neg_title = re.search(r"\[부정\]:\s*(.+)", answer)
                pos_title = pos_title.group(1).strip() if pos_title else ""
                neg_title = neg_title.group(1).strip() if neg_title else ""

                for a in target_articles:
                    if pos_title and pos_title in a["title"]:
                        result.append({
                            "회사명": comp,
                            "감성": "긍정",
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
                    if neg_title and neg_title in a["title"]:
                        result.append({
                            "회사명": comp,
                            "감성": "부정",
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
            except Exception:
                continue
    return result

def extract_article_text(url):
    # 네이버, 다음 등 포털 뉴스 중계 URL은 본문 추출 실패 - 바로 오류 반환
    PORTAL_DOMAINS = ["news.naver.com", "n.news.naver.com", "news.daum.net"]
    if any(domain in url for domain in PORTAL_DOMAINS):
        return "본문 추출 오류: 포털 뉴스 중계 URL입니다. 'originallink'를 사용하세요."
    try:
        article = newspaper.Article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        return f"본문 추출 오류: {e}"
    
def extract_keyword_from_link(search_results, article_link):
    """
    뉴스검색결과 dict와 기사 링크로 해당 기사의 키워드(회사명/카테고리)를 추출
    """
    for kw, arts in search_results.items():
        for art in arts:
            if art.get("link") == article_link:
                return kw
    return ""

def build_important_excel_same_format(important_articles, favorite_categories, excel_company_categories, search_results):
    """
    중요기사 목록을 '맞춤 양식' 엑셀 파일로 생성하여 BytesIO 반환
    """
    # DataFrame 생성
    df = pd.DataFrame(important_articles)

    # 맞춤 열 목록 지정
    columns = ["산업대분류", "산업소분류", "회사명", "감성", "제목", "링크", "날짜", "출처"]
    for col in columns:
        if col not in df.columns:
            df[col] = ""

    # 산업분류/소분류 자동 채우기
    for idx, row in df.iterrows():
        company = row["회사명"]
        sub_cat, major_cat = "", ""
        # 소분류 찾기
        for sub, comps in favorite_categories.items():
            if company in comps:
                sub_cat = sub
                break
        # 대분류 찾기
        for major, subs in excel_company_categories.items():
            if sub_cat in subs:
                major_cat = major
                break
        df.at[idx, "산업대분류"] = major_cat
        df.at[idx, "산업소분류"] = sub_cat

    # 날짜 포맷 변환
    if "날짜" in df.columns:
        try:
            df["날짜"] = pd.to_datetime(df["날짜"]).dt.strftime("%Y-%m-%d")
        except:
            pass

    # 최종 열 순서
    df = df[columns]

    # 엑셀 생성 및 스타일 지정
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="중요뉴스")
        workbook = writer.book
        worksheet = writer.sheets["중요뉴스"]

        header_format = workbook.add_format({
            "bold": True, "bg_color": "#DCE6F1", "border": 1,
            "align": "center", "valign": "vcenter"
        })
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 20)

    output.seek(0)
    return output
   
def matched_filter_keywords(article, common_keywords, industry_keywords):
    """
    기사 제목/요약/본문에서 실제로 포함된 필터 키워드 리스트 반환
    """
    text_candidates = [
        article.get("title", ""),
        article.get("description", ""),
        article.get("요약본", ""),
        article.get("요약", ""),
        article.get("full_text", ""),
        article.get("content", ""),
    ]
    text_long = " ".join([str(t) for t in text_candidates if t])
    matched_common = [kw for kw in common_keywords if kw in text_long]
    matched_industry = [kw for kw in industry_keywords if kw in text_long]
    return list(set(matched_common + matched_industry))


def render_articles_with_single_summary_and_telegram(
    results, show_limit, show_sentiment_badge=True, enable_summary=True
):
    SENTIMENT_CLASS = {"긍정": "sentiment-positive", "부정": "sentiment-negative"}
    col_list, col_summary = st.columns([1, 1])

    # ---------------------------- 뉴스 목록 열 ----------------------------
    with col_list:
        st.markdown("### 🔍 뉴스 검색 결과")
        for keyword, articles in results.items():
            # ✅ 접었다/펼칠 수 있는 expander로 변경
            with st.expander(f"[{keyword}] ({len(articles)}건)", expanded=True):
                # 전체 선택/해제 체크박스
                all_article_keys = []
                for idx, article in enumerate(articles):
                    uid = re.sub(r"\W+", "", article["link"])[-16:]
                    key = f"{keyword}_{idx}_{uid}"
                    all_article_keys.append(key)

                prev_value = all(st.session_state.article_checked.get(k, False) for k in all_article_keys)
                # 현재 상태(유저가 실제로 클릭한 후의 값)
                select_all = st.checkbox(
                    f"전체 기사 선택/해제 ({keyword})",
                    value=prev_value,
                    key=f"{keyword}_select_all"
                )
                # 클릭 변화 감지 — 한 번의 클릭에 즉시 처리!
                if select_all != prev_value:
                    for k in all_article_keys:
                        st.session_state.article_checked[k] = select_all
                        st.session_state.article_checked_left[k] = select_all
                    st.rerun()  # 즉시 리렌더링

                # 개별 기사 체크박스
                for idx, article in enumerate(articles):
                    uid = re.sub(r"\W+", "", article["link"])[-16:]
                    key = f"{keyword}_{idx}_{uid}"
                    cache_key = f"summary_{key}"
                    cols = st.columns([0.04, 0.96])
                    with cols[0]:
                        checked = st.checkbox(
                            "",
                            value=st.session_state.article_checked.get(key, False),
                            key=f"news_{key}",
                        )
                    with cols[1]:
                        sentiment = ""
                        if show_sentiment_badge and cache_key in st.session_state:
                            _, _, sentiment, _ = st.session_state[cache_key]
                        badge_html = (
                            f"<span class='sentiment-badge {SENTIMENT_CLASS.get(sentiment, 'sentiment-negative')}'>{sentiment}</span>"
                            if sentiment else ""
                        )
                        st.markdown(
                            f"<span class='news-title'><a href='{article['link']}' target='_blank'>{article['title']}</a></span> "
                            f"{badge_html} {article['date']} | {article['source']}",
                            unsafe_allow_html=True,
                        )
                    st.session_state.article_checked_left[key] = checked
                    st.session_state.article_checked[key] = checked

    # ---------------------------- 선택 기사 요약 열 ----------------------------
    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):

            # 1) 현재 선택된 기사 목록 수집
            selected_to_process = []
            industry_keywords_all = []
            if st.session_state.get("use_industry_filter", False):
                for sublist in st.session_state.industry_major_sub_map.values():
                    industry_keywords_all.extend(sublist)

            for keyword, articles in results.items():
                for idx, article in enumerate(articles):
                    uid = re.sub(r"\W+", "", article["link"])[-16:]
                    key = f"{keyword}_{idx}_{uid}"
                    if st.session_state.article_checked.get(key, False):
                        selected_to_process.append((keyword, idx, article))

            # 2) 병렬 처리로 요약/감성분석
            def process_article(item):
                keyword, idx, art = item
                cache_key = f"summary_{keyword}_{idx}_" + re.sub(r"\W+", "", art["link"])[-16:]
                if cache_key in st.session_state:
                    one_line, summary, sentiment, full_text = st.session_state[cache_key]
                else:
                    one_line, summary, sentiment, full_text = summarize_article_from_url(
                        art["link"], art["title"], do_summary=enable_summary
                    )
                    st.session_state[cache_key] = (one_line, summary, sentiment, full_text)

                filter_hits = matched_filter_keywords(
                    {"title": art["title"], "요약본": summary, "요약": one_line, "full_text": full_text},
                    ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords_all
                )
                return {
                    "키워드": keyword,
                    "필터히트": ", ".join(filter_hits),
                    "기사제목": safe_title(art["title"]),
                    "요약": one_line,
                    "요약본": summary,
                    "감성": sentiment,
                    "링크": art["link"],
                    "날짜": art["date"],
                    "출처": art["source"],
                    "full_text": full_text or "",
                }

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=10) as executor:
                selected_articles = list(executor.map(process_article, selected_to_process))

            # 3) 전체 결과 렌더링
            for art in selected_articles:
                st.markdown(
                    f"#### <span class='news-title'><a href='{art['링크']}' target='_blank'>{art['기사제목']}</a></span> "
                    f"<span class='sentiment-badge {SENTIMENT_CLASS.get(art['감성'], 'sentiment-negative')}'>{art['감성']}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"- **검색 키워드:** `{art['키워드']}`")
                st.markdown(f"- **필터로 인식된 키워드:** `{art['필터히트'] or '없음'}`")
                st.markdown(f"- **날짜/출처:** {art['날짜']} | {art['출처']}")
                if enable_summary:
                    st.markdown(f"- **한 줄 요약:** {art['요약']}")
                st.markdown(f"- **감성분석:** `{art['감성']}`")
                st.markdown("---")

            st.session_state.selected_articles = selected_articles
            st.write(f"선택된 기사 개수: {len(selected_articles)}")

            # 다운로드 / 전체 해제 버튼
            col_dl1, col_dl2 = st.columns([0.55, 0.45])
            with col_dl1:
                st.download_button(
                    label="📥 맞춤 엑셀 다운로드",
                    data=get_excel_download_with_favorite_and_excel_company_col(
                        selected_articles, favorite_categories, excel_company_categories,
                        st.session_state.search_results
                    ).getvalue(),
                    file_name="뉴스요약_맞춤형.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            with col_dl2:
                if st.button("🗑 선택 해제 (전체)"):
                    for key in list(st.session_state.article_checked.keys()):
                        st.session_state.article_checked[key] = False
                    for key in list(st.session_state.article_checked_left.keys()):
                        st.session_state.article_checked_left[key] = False
                    st.rerun()

        render_important_article_review_and_download()

def render_important_article_review_and_download():
    with st.container(border=True):
        st.markdown("### ⭐ 중요 기사 리뷰 및 편집")

        # 중요기사 자동선정 버튼
        auto_btn = st.button("🚀 OpenAI 기반 중요 기사 자동 선정")
        if auto_btn:
            with st.spinner("OpenAI로 중요 뉴스 선정 중..."):
                filtered_results_for_important = {}
                for keyword, articles in st.session_state.search_results.items():
                    filtered_articles = [a for a in articles if article_passes_all_filters(a)]
                    if st.session_state.get("remove_duplicate_articles", False):
                        filtered_articles = remove_duplicates(filtered_articles)
                    if filtered_articles:
                        filtered_results_for_important[keyword] = filtered_articles

                important_articles = generate_important_article_list(
                    search_results=filtered_results_for_important,
                    common_keywords=ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords=st.session_state.get("industry_sub", []),
                    favorites=favorite_categories
                )
                # key naming 통일
                for i, art in enumerate(important_articles):
                    important_articles[i] = {
                        "키워드": art.get("키워드") or art.get("회사명") or art.get("keyword") or "",
                        "기사제목": art.get("기사제목") or art.get("제목") or art.get("title") or "",
                        "감성": art.get("감성", ""),
                        "링크": art.get("링크") or art.get("link", ""),
                        "날짜": art.get("날짜") or art.get("date", ""),
                        "출처": art.get("출처") or art.get("source", "")
                    }

                st.session_state["important_articles_preview"] = important_articles
                st.session_state["important_selected_index"] = []

        articles = st.session_state.get("important_articles_preview", [])
        selected_indexes = st.session_state.get("important_selected_index", [])

        st.markdown("🎯 **중요 기사 목록** (교체 또는 삭제할 항목을 체크하세요)")

        # ====== 병렬 요약 준비 ======
        from concurrent.futures import ThreadPoolExecutor
        one_line_map = {}
        to_summarize = []

        for idx, article in enumerate(articles):
            link = article.get("링크", "")
            cleaned_id = re.sub(r"\W+", "", link)[-16:] if link else ""
            in_cache = False
            for k, v in st.session_state.items():
                if k.startswith("summary_") and cleaned_id in k and isinstance(v, tuple):
                    one_line_map[idx] = v[0]
                    in_cache = True
                    break
            if not in_cache and link:
                to_summarize.append((idx, link, article.get("기사제목", "")))

        if to_summarize:
            with st.spinner("중요 기사 요약 생성 중..."):
                def get_one_line(args):
                    idx, link, title = args
                    one_line, _, _, _ = summarize_article_from_url(link, title, do_summary=True)
                    return idx, one_line

                with ThreadPoolExecutor(max_workers=10) as executor:
                    for idx, one_line in executor.map(get_one_line, to_summarize):
                        one_line_map[idx] = one_line
        # ====== 병렬 요약 완료 ======

        new_selection = []
        for idx, article in enumerate(articles):
            checked = st.checkbox(
                f"{article.get('키워드', '')} | {article.get('감성', '')} | {article.get('기사제목', '')}",
                key=f"important_chk_{idx}",
                value=(idx in selected_indexes)
            )
            if idx in one_line_map and one_line_map[idx]:
                st.markdown(
                    f"<span style='color:gray;font-style:italic;'>{one_line_map[idx]}</span>",
                    unsafe_allow_html=True
                )
            if checked:
                new_selection.append(idx)

        st.session_state["important_selected_index"] = new_selection
        st.markdown("---")

        col_add, col_del, col_rep = st.columns([0.3, 0.35, 0.35])
        # ➕ 선택 기사 추가
        with col_add:
            if st.button("➕ 선택 기사 추가"):
                left_selected_keys = [k for k, v in st.session_state.article_checked_left.items() if v]
                if not left_selected_keys:
                    st.warning("왼쪽 뉴스검색 결과에서 적어도 1개 이상 선택해 주세요.")
                else:
                    added_count = 0
                    important = st.session_state.get("important_articles_preview", [])
                    for from_key in left_selected_keys:
                        m = re.match(r"^[^_]+_[0-9]+_(.+)$", from_key)
                        if not m:
                            continue
                        key_tail = m.group(1)
                        selected_article, article_link = None, None
                        for kw, arts in st.session_state.search_results.items():
                            for art in arts:
                                uid = re.sub(r'\W+', '', art['link'])[-16:]
                                if uid == key_tail:
                                    selected_article = art
                                    article_link = art["link"]
                                    break
                            if selected_article:
                                break
                        if not selected_article:
                            continue

                        keyword = extract_keyword_from_link(st.session_state.search_results, article_link)
                        cleaned_id = re.sub(r'\W+', '', selected_article['link'])[-16:]
                        sentiment = None
                        for k in st.session_state.keys():
                            if k.startswith("summary_") and cleaned_id in k:
                                sentiment = st.session_state[k][2]
                                break
                        if not sentiment:
                            _, _, sentiment, _ = summarize_article_from_url(
                                selected_article["link"], selected_article["title"]
                            )

                        new_article = {
                            "키워드": keyword,
                            "기사제목": selected_article["title"],
                            "감성": sentiment or "",
                            "링크": selected_article["link"],
                            "날짜": selected_article["date"],
                            "출처": selected_article["source"]
                        }
                        if not any(a["링크"] == new_article["링크"] for a in important):
                            important.append(new_article)
                            added_count += 1
                        st.session_state.article_checked_left[from_key] = False
                        st.session_state.article_checked[from_key] = False

                    st.session_state["important_articles_preview"] = important
                    if added_count > 0:
                        st.success(f"{added_count}건의 기사가 중요 기사 목록에 추가되었습니다.")
                    else:
                        st.info("추가된 새로운 기사가 없습니다.")
                    st.rerun()

        # 🗑 선택 기사 삭제
        with col_del:
            if st.button("🗑 선택 기사 삭제"):
                important = st.session_state.get("important_articles_preview", [])
                for idx in sorted(st.session_state["important_selected_index"], reverse=True):
                    if 0 <= idx < len(important):
                        important.pop(idx)
                st.session_state["important_articles_preview"] = important
                st.session_state["important_selected_index"] = []
                st.rerun()

        # 🔁 선택 기사 교체
        with col_rep:
            if st.button("🔁 선택 기사 교체"):
                left_selected_keys = [k for k, v in st.session_state.article_checked_left.items() if v]
                right_selected_indexes = st.session_state["important_selected_index"]
                if len(left_selected_keys) != 1 or len(right_selected_indexes) != 1:
                    st.warning("왼쪽 1개, 오른쪽 1개만 선택해주세요.")
                    return
                from_key = left_selected_keys[0]
                target_idx = right_selected_indexes[0]
                m = re.match(r"^[^_]+_[0-9]+_(.+)$", from_key)
                if not m:
                    st.warning("기사 식별자 파싱 실패")
                    return
                key_tail = m.group(1)
                selected_article, article_link = None, None
                for kw, art_list in st.session_state.search_results.items():
                    for art in art_list:
                        uid = re.sub(r'\W+', '', art['link'])[-16:]
                        if uid == key_tail:
                            selected_article = art
                            article_link = art["link"]
                            break
                    if selected_article:
                        break
                if not selected_article:
                    st.warning("왼쪽에서 선택한 기사 정보를 찾을 수 없습니다.")
                    return

                keyword = extract_keyword_from_link(st.session_state.search_results, article_link)
                cleaned_id = re.sub(r'\W+', '', selected_article['link'])[-16:]
                sentiment = None
                for k in st.session_state.keys():
                    if k.startswith("summary_") and cleaned_id in k:
                        sentiment = st.session_state[k][2]
                        break
                if not sentiment:
                    _, _, sentiment, _ = summarize_article_from_url(
                        selected_article["link"], selected_article["title"]
                    )

                new_article = {
                    "키워드": keyword,
                    "기사제목": selected_article["title"],
                    "감성": sentiment or "",
                    "링크": selected_article["link"],
                    "날짜": selected_article["date"],
                    "출처": selected_article["source"]
                }
                st.session_state["important_articles_preview"][target_idx] = new_article
                st.session_state.article_checked_left[from_key] = False
                st.session_state.article_checked[from_key] = False
                st.session_state["important_selected_index"] = []
                st.success("중요 기사 교체 완료")
                st.rerun()

        # --- 맞춤 양식 동일 포맷 엑셀 다운로드 ---
        st.markdown("---")
        st.markdown("📥 **리뷰한 중요 기사들을 엑셀로 다운로드하세요.**")

        final_selected_indexes = st.session_state.get("important_selected_index", [])
        articles_source = st.session_state.get("important_articles_preview", [])

        # 산업 키워드 전체 수집 (필터용)
        industry_keywords_all = []
        if st.session_state.get("use_industry_filter", False):
            for sublist in st.session_state.industry_major_sub_map.values():
                industry_keywords_all.extend(sublist)
        
        def enrich_article_for_excel(raw_article):
            link = raw_article.get("링크", "")
            keyword = raw_article.get("키워드", "")
            cleaned_id = re.sub(r"\W+", "", link)[-16:]
            sentiment, one_line, summary, full_text = None, "", "", ""

            # 캐시에서 요약/감성 꺼내오기
            for k, v in st.session_state.items():
                if k.startswith("summary_") and cleaned_id in k and isinstance(v, tuple):
                    one_line, summary, sentiment, full_text = v
                    break
            # 없으면 직접 분석
            if not sentiment:
                one_line, summary, sentiment, full_text = summarize_article_from_url(
                    link, raw_article.get("기사제목", "")
                )

            filter_hits = matched_filter_keywords(
                {"title": raw_article.get("기사제목", ""), "요약본": summary,
                 "요약": one_line, "full_text": full_text},
                ALL_COMMON_FILTER_KEYWORDS,
                industry_keywords_all
            )
            return {
                "키워드": keyword,
                "필터히트": ", ".join(filter_hits),
                "기사제목": safe_title(raw_article.get("기사제목", "")),
                "요약": one_line,
                "요약본": summary,
                "감성": sentiment,
                "링크": link,
                "날짜": raw_article.get("날짜", ""),
                "출처": raw_article.get("출처", ""),
                "full_text": full_text or "",
            }
        
        # ✅ 모든 '중요 기사 리스트'를 엑셀 summary_data 구조로 변환
        #    → 선택/비선택과 관계없이 전체 리스트 사용 가능
        summary_data = [enrich_article_for_excel(a) for a in articles_source]

        # 🔹 favorite_categories / excel_company_categories 순서에 맞춰 모든 기업 출력
        excel_data = get_excel_download_with_favorite_and_excel_company_col(
            summary_data,
            favorite_categories,
            excel_company_categories,
            st.session_state.search_results
        )

        st.download_button(
            label="📥 중요 기사 최종 엑셀 다운로드 (맞춤 양식)",
            data=excel_data.getvalue(),
            file_name=f"중요뉴스_최종선정_양식_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if st.session_state.search_results:
    filtered_results = {}
    for keyword, articles in st.session_state.search_results.items():
        filtered_articles = [a for a in articles if article_passes_all_filters(a)]
        
        # --- 중복 기사 제거 처리 ---
        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)
        
        if filtered_articles:
            filtered_results[keyword] = filtered_articles

    render_articles_with_single_summary_and_telegram(
        filtered_results,
        st.session_state.show_limit,
        show_sentiment_badge=st.session_state.get("show_sentiment_badge", False),
        enable_summary=st.session_state.get("enable_summary", True)
    )
