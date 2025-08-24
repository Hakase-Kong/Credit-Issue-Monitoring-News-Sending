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

        # 병렬 처리 시작
        with ThreadPoolExecutor(max_workers=min(5, len(kw_list))) as executor:
            futures = {
                executor.submit(fetch_naver_news, search_kw, start_date, end_date, 
                                require_keyword_in_title=require_keyword_in_title): search_kw
                for search_kw in kw_list
            }
            for future in as_completed(futures):
                search_kw = futures[future]
                try:
                    fetched = future.result()
                    # 각 기사에 검색어 정보 추가
                    fetched = [{**a, "검색어": search_kw} for a in fetched]
                    all_articles.extend(fetched)
                except Exception as e:
                    st.warning(f"{main_kw} - '{search_kw}' 검색 실패: {e}")

        # 중복 제거 여부
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

def summarize_and_sentiment_with_openai(text, do_summary=True, target_keyword=None):
    """
    본문 요약/감성분석.
    target_keyword: 감성 판단의 초점을 맞출 기업/키워드
    """
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", "", "감성 추출 실패", text
    if not text or "본문 추출 오류" in text:
        return "기사 본문이 추출 실패", "", "감성 추출 실패", text

    lang = detect_lang(text)

    # 🔹 프롬프트 구성: target_keyword를 중심으로 감성 판정
    if lang == "ko":
        focus_info = f" 분석의 초점은 반드시 '{target_keyword}' 기업(또는 키워드)이며, 기사의 전체 분위기가 아닌 이 기업에 대한 기사 내용과 문맥을 기준으로 감성을 판정해야 합니다." if target_keyword else ""
        role_prompt = (
            "너는 경제 뉴스 요약/분석 전문가야."
            " 한 문장 요약에는 반드시 주체, 핵심 사건, 결과를 포함하고,"
            " 감성 분류는 해당 기업에 긍정/부정 영향을 주는지를 판단해야 한다."
            + focus_info +
            " 감성은 '긍정' 또는 '부정' 중 하나만 선택. 중립은 금지."
        )
        main_prompt = f"""
아래 기사 본문을 분석해 다음 세 가지를 정확히 응답하라.
대상 기업/키워드: "{target_keyword or 'N/A'}"

[한 줄 요약]: 대상 기업에 대한 주요 사건과 결과 포함
[검색 키워드]: 이 기사가 검색에 사용된 키워드를 콤마(,)로 명시
[감성]: 대상 기업에 긍정 또는 부정 (둘 중 하나만)
[주요 키워드]: 인물, 기업, 조직명만 콤마(,)로, 없으면 없음

[기사 본문]
{text}
"""
    else:
        focus_info = f" Focus strictly on sentiment toward '{target_keyword}' (the entity), not the overall industry tone." if target_keyword else ""
        role_prompt = (
            "You are a financial news summarization expert."
            " Your summary must highlight the entity, key event, and result."
            " Sentiment classification must reflect the impact on the specific entity of interest."
            + focus_info +
            " Sentiment must be either positive or negative. Neutral is not allowed."
        )
        main_prompt = f"""
Analyze the following article focusing on this target entity: "{target_keyword or 'N/A'}"

[One-line Summary]: Include event and outcome relevant to the target entity
[Search Keywords]: Keywords that retrieved this article
[Sentiment]: positive or negative (based ONLY on the target entity's context)
[Key Entities]: Companies/people/org mentioned, comma separated

[ARTICLE]
{text}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": role_prompt},
                {"role": "user", "content": main_prompt}
            ],
            max_tokens=900,
            temperature=0
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

def summarize_article_from_url(article_url, title, do_summary=True, target_keyword=None, description=None):
    cache_key_base = re.sub(r"\W+", "", article_url)[-16:]
    summary_key = f"summary_{cache_key_base}"

    if summary_key in st.session_state:
        return st.session_state[summary_key]

    try:
        # 🔹 fallback_title, fallback_desc 전달
        full_text = extract_article_text(article_url, fallback_desc=description, fallback_title=title)
        if full_text.startswith("본문 추출 오류"):
            result = (full_text, None, None, None)
        else:
            one_line, summary, sentiment, _ = summarize_and_sentiment_with_openai(
                full_text, do_summary=do_summary, target_keyword=target_keyword
            )
            result = (one_line, summary, sentiment, full_text)
    except Exception as e:
        result = (f"요약 오류: {e}", None, None, None)

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
    import difflib

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
    result = []

    # 유사도 기반 부분일치
    def match_title(target, candidates):
        # 완전일치 우선
        for cand in candidates:
            if cand == target:
                return True
        # 유사도 0.8 이상이면 true
        for cand in candidates:
            if difflib.SequenceMatcher(None, cand, target).ratio() >= 0.8:
                return True
        # 후보 제목 일부가 기사에 들어가도 인정 (5글자 이상)
        for cand in candidates:
            seg = cand[:min(8, len(cand))]
            if seg and seg in target:
                return True
        return False

    for category, companies in favorites.items():
        for comp in companies:
            articles = search_results.get(comp, [])
            filtered_keywords = list(set(common_keywords + industry_keywords))
            target_articles = [a for a in articles if any(kw in a["title"] for kw in filtered_keywords)]

            if not target_articles:
                continue

            prompt_list = "\n".join([f"{i+1}. {a['title']} - {a['link']}" for i, a in enumerate(target_articles)])

            prompt = (
                f"[기사 목록]\n{prompt_list}\n\n"
                "각 키워드(혹은 회사)별로 [긍정 기사 최대 3건], [부정 기사 최대 3건]씩 선정하세요.\n"
                "- 긍정은 신용등급 방어나 실적 개선에, 부정은 리스크 확대나 수익성 악화에 영향을 줄 인상적 이슈 기사 우선 선정\n"
                "- 제목이 중복/유사한 기사는 한 번만 선택\n"
                "- 각 항목별로 없으면 공란으로 남기세요.\n"
                "\n[선택결과 출력형식]\n"
                "[긍정]:\n1. (기사제목)\n2. (기사제목)\n3. (기사제목)\n[부정]:\n1. (기사제목)\n2. (기사제목)\n3. (기사제목)"
            )
            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0
                )
                answer = response.choices[0].message.content.strip()

                # 긍정/부정 번호제목 라인 robust 파싱
                def parse_titles(block):
                    titles = []
                    for line in block.strip().split("\n"):
                        m = re.match(r"([0-9]+)\.\s*(.+)", line.strip())
                        if m:
                            titles.append(m.group(2).strip())
                    return titles

                # 긍정 블럭 추출  
                pos_titles = []
                m_pos = re.search(r"\[긍정\]:\s*((?:[0-9]+\..+\n?)*)", answer)
                if m_pos:
                    pos_titles = parse_titles(m_pos.group(1))

                # 부정 블럭 추출
                neg_titles = []
                m_neg = re.search(r"\[부정\]:\s*((?:[0-9]+\..+\n?)*)", answer)
                if m_neg:
                    neg_titles = parse_titles(m_neg.group(1))

                # 기사제목과 부분일치(유사도) 매칭
                for a in target_articles:
                    is_positive = any(match_title(a["title"], [t]) for t in pos_titles)
                    is_negative = any(match_title(a["title"], [t]) for t in neg_titles)
                
                    # 긍정 ⇨ 부정 우선순위는 논의대로 맞춰 적용(여기선 "긍정 우선")
                    if is_positive and not is_negative:
                        result.append({
                            "회사명": comp,
                            "감성": "긍정",
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
                    elif is_negative and not is_positive:
                        result.append({
                            "회사명": comp,
                            "감성": "부정",
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
                    # is_positive and is_negative 모두 True면, "긍정" 또는 "부정"만 추가 (여기선 긍정)
                    elif is_positive and is_negative:
                        result.append({
                            "회사명": comp,
                            "감성": "긍정",  # 또는 "부정"으로 교체 가능
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
                    # 둘다 False면 무시
            except Exception as e:
                print("OpenAI 중요기사 자동선정 오류:", e)
                continue
    return result

def extract_article_text(url, fallback_desc=None, fallback_title=None):
    """
    뉴스 기사 본문을 최대한 정확하게 추출
    url: 기사 원문 URL
    fallback_desc, fallback_title: 본문 추출 실패시 사용할 검색 API의 요약/제목
    """
    # 포털 뉴스 차단
    PORTAL_DOMAINS = ["news.naver.com", "n.news.naver.com", "news.daum.net"]
    if any(domain in url for domain in PORTAL_DOMAINS):
        return f"본문 추출 오류: 포털 뉴스 중계 URL입니다. originallink 사용 권장."

    try:
        # 1차 시도: newspaper3k
        article = newspaper.Article(url, language='ko')
        article.download()
        article.parse()
        text = article.text.strip()

        # 불필요 문구 제거
        text = re.sub(r"\S+@\S+", "", text)  # 이메일 제거
        text = re.sub(r"▶.*", "", text)      # '▶'로 시작하는 행 제거
        text = re.sub(r"(무단전재\s*및\s*재배포\s*금지.*$)", "", text)

        # 2차: 텍스트 길이 검증 (글자가 너무 짧으면 fallback)
        if len(text) < 100 and fallback_desc:
            # 너무 짧으면 설명(description) 붙여서 보완
            text = text + "\n\n" + fallback_desc
        
        return text

    except Exception as e:
        # 2차 시도: 직접 HTML 파싱
        try:
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            # 대표적인 한국 언론 본문 영역 선택자
            selectors = [
                "div#articleBodyContents", 
                "div.article_body", 
                "div#newsEndContents",
                "div[itemprop='articleBody']"
            ]
            for sel in selectors:
                body = soup.select_one(sel)
                if body:
                    # 텍스트 정제
                    text = " ".join(body.get_text(separator=" ").split())
                    text = re.sub(r"\S+@\S+", "", text)
                    if len(text) > 200:
                        return text
        except Exception:
            pass

        # fallback: 제목 + 설명이라도 제공
        if fallback_title or fallback_desc:
            return f"[기사제목] {fallback_title or ''}\n[요약정보] {fallback_desc or ''}"

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

        # favorite_categories 순서대로 대분류/기업 출력
        for category_name, company_list in favorite_categories.items():
            companies_with_results = [c for c in company_list if c in results]
            if not companies_with_results:
                continue
            with st.expander(f"📂 {category_name}", expanded=True):
                for company in companies_with_results:
                    articles = results[company]
                    with st.expander(f"[{company}] ({len(articles)}건)", expanded=False):
                        all_article_keys = []
                        for idx, article in enumerate(articles):
                            uid = re.sub(r"\W+", "", article["link"])[-16:]
                            key = f"{company}_{idx}_{uid}"
                            all_article_keys.append(key)

                        prev_value = all(st.session_state.article_checked.get(k, False) for k in all_article_keys)
                        select_all = st.checkbox(
                            f"전체 기사 선택/해제 ({company})",
                            value=prev_value,
                            key=f"{company}_select_all"
                        )
                        if select_all != prev_value:
                            for k in all_article_keys:
                                st.session_state.article_checked[k] = select_all
                                st.session_state.article_checked_left[k] = select_all
                            st.rerun()

                        for idx, article in enumerate(articles):
                            uid = re.sub(r"\W+", "", article["link"])[-16:]
                            key = f"{company}_{idx}_{uid}"
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
                                search_word_info = f" | 검색어: {article.get('검색어', '')}" if article.get("검색어") else ""
                                st.markdown(
                                    f"<span class='news-title'><a href='{article['link']}' target='_blank'>{article['title']}</a></span> "
                                    f"{badge_html} {article['date']} | {article['source']}{search_word_info}",
                                    unsafe_allow_html=True,
                                )
                            st.session_state.article_checked_left[key] = checked
                            st.session_state.article_checked[key] = checked

    # ---------------------------- 선택 기사 요약 열 ----------------------------
    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):

            industry_keywords_all = []
            if st.session_state.get("use_industry_filter", False):
                for sublist in st.session_state.industry_major_sub_map.values():
                    industry_keywords_all.extend(sublist)

            # 선택된 기사 그룹핑
            grouped_selected = {}
            for cat_name, company_list in favorite_categories.items():
                for company in company_list:
                    if company in results:
                        for idx, article in enumerate(results[company]):
                            uid = re.sub(r"\W+", "", article["link"])[-16:]
                            key = f"{company}_{idx}_{uid}"
                            if st.session_state.article_checked.get(key, False):
                                grouped_selected.setdefault(cat_name, {}).setdefault(company, []).append(
                                    (company, idx, article)
                                )

            # 병렬 요약 처리
            def process_article(item):
                keyword, idx, art = item
                cache_key = f"summary_{keyword}_{idx}_" + re.sub(r"\W+", "", art["link"])[-16:]
                if cache_key in st.session_state:
                    one_line, summary, sentiment, full_text = st.session_state[cache_key]
                else:
                    # 🔹 keyword를 target_keyword로 전달
                    one_line, summary, sentiment, full_text = summarize_article_from_url(
                        art["link"], art["title"], do_summary=enable_summary, target_keyword=keyword
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
            for cat_name, comp_map in grouped_selected.items():
                for company, items in comp_map.items():
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        grouped_selected[cat_name][company] = list(executor.map(process_article, items))

            total_selected_count = 0
            for cat_name, comp_map in grouped_selected.items():
                with st.expander(f"📂 {cat_name}", expanded=True):
                    for company, arts in comp_map.items():
                        with st.expander(f"[{company}] ({len(arts)}건)", expanded=True):
                            for art in arts:
                                total_selected_count += 1
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

            st.session_state.selected_articles = [
                art for comp_map in grouped_selected.values() for arts in comp_map.values() for art in arts
            ]
            st.write(f"선택된 기사 개수: {total_selected_count}")

            # 다운로드 / 전체 해제
            col_dl1, col_dl2 = st.columns([0.55, 0.45])
            with col_dl1:
                st.download_button(
                    label="📥 맞춤 엑셀 다운로드",
                    data=get_excel_download_with_favorite_and_excel_company_col(
                        st.session_state.selected_articles,
                        favorite_categories,
                        excel_company_categories,
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
    # 여백 최소화 CSS (한 번만 선언, 중복 선언 시는 위쪽 선언 삭제)
    st.markdown("""
        <style>
        [data-testid="stVerticalBlock"] > div {margin-bottom: 0.05rem !important;}
        .stCheckbox {margin-bottom: 0.03rem!important;}
        .stMarkdown {margin-bottom: 0.05rem !important;}
        .stExpanderContent {padding-top:0.01rem!important; padding-bottom:0.01rem!important;}
        </style>
    """, unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("### ⭐ 중요 기사 리뷰 및 편집")

        # 중요기사 자동선정 버튼
        auto_btn = st.button("🚀 OpenAI 기반 중요 기사 자동 선정")
        if auto_btn:
            with st.spinner("OpenAI로 중요 뉴스 선정 중..."):
                # 이 줄만 바꿔주세요!
                filtered_results_for_important = st.session_state.get('filtered_results', {})
                important_articles = generate_important_article_list(
                    search_results=filtered_results_for_important,
                    common_keywords=ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords=st.session_state.get("industry_sub", []),
                    favorites=favorite_categories
                )
                # key명 통일
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
        if not articles:
            st.info("자동선정된 중요 기사가 없습니다. 필터 기준 또는 선정 프롬프트/파싱 코드를 점검해주세요.")
            return

        selected_indexes = st.session_state.get("important_selected_index", [])

        st.markdown("🎯 **중요 기사 목록** (키워드별 분류, 교체/삭제/추가 반영)")

        # 키워드별 기사 그룹핑 (favorite_categories 순서 유지)
        from collections import defaultdict
        grouped = defaultdict(list)
        for idx, article in enumerate(articles):
            kw = article.get("키워드") or article.get("회사명") or "기타"
            grouped[kw].append((idx, article))

        ordered_keywords = list(favorite_categories.keys())
        shown_keywords = [kw for kw in ordered_keywords if kw in grouped]
        etc_keywords = [kw for kw in grouped if kw not in shown_keywords]
        # ETC 키워드는 favorite_categories 순서 밖이므로 정렬하지 않고 그대로 뒤에 배치
        all_keywords = shown_keywords + etc_keywords

        # 병렬로 요약 한번에 미리 처리 (OpenAI 호출 캐시 활용)
        from concurrent.futures import ThreadPoolExecutor

        def summarize_for_render(idx_and_art):
            idx, article = idx_and_art
            cleaned_id = re.sub(r"\W+", "", article.get("링크", ""))[-16:]
            summary_key = f"summary_{cleaned_id}"
            if summary_key in st.session_state and isinstance(st.session_state[summary_key], tuple):
                one_line, _, sentiment, _ = st.session_state[summary_key]
            else:
                one_line, _, sentiment, _ = summarize_article_from_url(
                    article.get("링크", ""),             # 링크
                    article.get("기사제목", ""),         # 타이틀
                    do_summary=True,                     # 요약 always
                    target_keyword=article.get("키워드", "") # 핵심키워드(회사명 등)
                )
                st.session_state[summary_key] = (one_line, None, sentiment, None)
            return idx, article, one_line, sentiment
        
        # summary_for_render를 통해 한 줄 요약/감성 동시 제공
        for kw in all_keywords:
            items = grouped[kw]
            with ThreadPoolExecutor(max_workers=8) as executor:
                grouped[kw] = list(executor.map(summarize_for_render, items))
        
            with st.expander(f"[{kw}] ({len(items)}건)", expanded=False):
                for idx, article, one_line, sentiment in grouped[kw]:
                    # 라벨에 감성 및 요약 모두 표기
                    label = (
                        f"{sentiment} | "
                        f"<a href='{article.get('링크')}' target='_blank'>{article.get('기사제목', '')}</a><br>"
                        f"<span style='color:gray;font-style:italic;font-size:0.94em'>{one_line}</span>"
                    )
                    st.markdown(label, unsafe_allow_html=True)
                    st.write("")

                    # 체크박스 상태 동기화 (rerun 없이 session_state만 갱신)
                    if cb:
                        if idx not in selected_indexes:
                            selected_indexes.append(idx)
                    else:
                        if idx in selected_indexes:
                            selected_indexes.remove(idx)

        # 최종 선택된 인덱스 세션 저장
        st.session_state["important_selected_index"] = selected_indexes

        # 하단 작업 버튼 및 엑셀 다운로드 UI (기존과 동일)
        col_add, col_del, col_rep = st.columns([0.3, 0.35, 0.35])

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
                        selected_article = None
                        for kw, arts in st.session_state.search_results.items():
                            for art in arts:
                                uid = re.sub(r'\W+', '', art['link'])[-16:]
                                if uid == key_tail:
                                    selected_article = art
                                    break
                            if selected_article:
                                break
                        if not selected_article:
                            continue

                        keyword = extract_keyword_from_link(st.session_state.search_results, selected_article["link"])
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

        with col_del:
            if st.button("🗑 선택 기사 삭제"):
                important = st.session_state.get("important_articles_preview", [])
                for idx in sorted(st.session_state["important_selected_index"], reverse=True):
                    if 0 <= idx < len(important):
                        important.pop(idx)
                st.session_state["important_articles_preview"] = important
                st.session_state["important_selected_index"] = []

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
                selected_article = None
                for kw, art_list in st.session_state.search_results.items():
                    for art in art_list:
                        uid = re.sub(r'\W+', '', art['link'])[-16:]
                        if uid == key_tail:
                            selected_article = art
                            break
                    if selected_article:
                        break
                if not selected_article:
                    st.warning("왼쪽에서 선택한 기사 정보를 찾을 수 없습니다.")
                    return

                keyword = extract_keyword_from_link(st.session_state.search_results, selected_article["link"])
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

        # 엑셀 다운로드 영역
        st.markdown("---")
        st.markdown("📥 **리뷰한 중요 기사들을 엑셀로 다운로드하세요.**")

        final_selected_indexes = st.session_state.get("important_selected_index", [])
        articles_source = st.session_state.get("important_articles_preview", [])

        industry_keywords_all = []
        if st.session_state.get("use_industry_filter", False):
            for sublist in st.session_state.industry_major_sub_map.values():
                industry_keywords_all.extend(sublist)

        def enrich_article_for_excel(raw_article):
            link = raw_article.get("링크", "")
            keyword = raw_article.get("키워드", "")
            cleaned_id = re.sub(r"\W+", "", link)[-16:]
            sentiment, one_line, summary, full_text = None, "", "", ""
            for k, v in st.session_state.items():
                if k.startswith("summary_") and cleaned_id in k and isinstance(v, tuple):
                    one_line, summary, sentiment, full_text = v
                    break
            if not sentiment:
                one_line, summary, sentiment, full_text = summarize_article_from_url(link, raw_article.get("기사제목", ""))
            filter_hits = matched_filter_keywords(
                {"title": raw_article.get("기사제목", ""), "요약본": summary, "요약": one_line, "full_text": full_text},
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

        summary_data = [enrich_article_for_excel(a) for a in articles_source]

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
        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)
        if filtered_articles:
            filtered_results[keyword] = filtered_articles

    # 여기에 저장
    st.session_state['filtered_results'] = filtered_results

    render_articles_with_single_summary_and_telegram(
        filtered_results,
        st.session_state.show_limit,
        show_sentiment_badge=st.session_state.get("show_sentiment_badge", False),
        enable_summary=st.session_state.get("enable_summary", True)
    )

