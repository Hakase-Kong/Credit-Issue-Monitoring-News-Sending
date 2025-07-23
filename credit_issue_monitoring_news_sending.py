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

def process_keywords_parallel(keyword_list, start_date, end_date, require_keyword_in_title=False):
    def fetch_and_store(k):
        return k, fetch_naver_news(k, start_date, end_date, require_keyword_in_title=require_keyword_in_title)
    with ThreadPoolExecutor(max_workers=min(5, len(keyword_list))) as executor:
        futures = [executor.submit(fetch_and_store, k) for k in keyword_list]
        for future in as_completed(futures):
            k, articles = future.result()
            st.session_state.search_results[k] = articles
            if k not in st.session_state.show_limit:
                st.session_state.show_limit[k] = 5

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

# --- 제외 키워드 ---
EXCLUDE_TITLE_KEYWORDS = [
    "야구", "축구", "배구", "농구", "골프", "e스포츠", "올림픽", "월드컵", "K리그", "프로야구", "프로축구", "프로배구", "프로농구", "유도",
    "부고", "부음", "인사", "승진", "임명", "발령", "인사발령", "인사이동",
    "브랜드평판", "브랜드 평판", "브랜드 순위", "브랜드지수",
    "코스피", "코스닥", "주가", "주식", "증시", "시세", "마감", "장중", "장마감", "거래량", "거래대금", "상한가", "하한가",
    "봉사", "후원", "기부", "우승", "무승부", "패배", "스포츠", "스폰서", "지속가능", "ESG", "위촉", "이벤트", "사전예약", "챔프전",
    "프로모션", "연극", "공연", "어르신", "링컨", "에비에이터", "NH퍼플통장", "골라담기",
    "음악회", "교향악단", "사이버대", "신진서", "안성준", "GS칼텍스배", "프로기전", "다문화", "와인25플러스", "과채주스", "책Dream", "책드림", "트로페오",
    "브랜드데이", "쇼핑라이브", "산학협력 컨퍼런스", "녹색상품", "소비자가 뽑은", "캠페인", "나눔", "챔피언십", "사회공헌", "성금"
]

# 필터링할 언론사 도메인 리스트 (www. 제거된 도메인 기준)
ALLOWED_SOURCES = {
    "news.einfomax.co.kr", "yna.co.kr", "newsis.com", "mk.co.kr", "news1.kr", "mt.co.kr", "hankyung.com",
    "joongang.co.kr", "chosun.com", "edaily.co.kr", "biz.heraldcorp.com", "kmib.co.kr", "seoul.co.kr",
    "thebell.co.kr", "fnnews.com", "ichannela.com", "donga.com", "hani.co.kr", "news.jtbc.co.kr",
    "khan.co.kr", "imnews.imbc.com", "mbn.co.kr", "news.sbs.co.kr", "tvchosun.com", "ytn.co.kr",
    "biz.chosun.com", "media.naver.com", "ohmynews.com", "ajunews.com", "sedaily.com", "asiae.co.kr",
    "hankookilbo.com", "nocutnews.co.kr", "bloter.net", "segye.com", "bizwatch.co.kr", "newsprime.co.kr",
    "meconomynews.com", "newsway.co.kr"
}

def exclude_by_title_keywords(title, exclude_keywords):
    for word in exclude_keywords:
        if word in title:
            return True
    return False

# --- 세션 상태 변수 초기화 ---
if "favorite_keywords" not in st.session_state:
    st.session_state.favorite_keywords = set()
if "search_results" not in st.session_state:
    st.session_state.search_results = {}
if "show_limit" not in st.session_state:
    st.session_state.show_limit = {}
if "search_triggered" not in st.session_state:
    st.session_state.search_triggered = False
if "selected_articles" not in st.session_state:
    st.session_state.selected_articles = []
if "cat_major_autoset" not in st.session_state:
    st.session_state.cat_major_autoset = []
if "important_articles_preview" not in st.session_state:
    st.session_state.important_articles_preview = []
if "important_selected_index" not in st.session_state:
    st.session_state["important_selected_index"] = []
if "article_checked_left" not in st.session_state:
    st.session_state.article_checked_left = {}

# --- 즐겨찾기 카테고리(변경 금지) ---
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

excel_company_categories = {
    "국/공채": [],
    "공공기관": [],
    "보험사": [
        "현대해상화재보험(후)", "농협생명보험(후)", "메리츠화재해상보험(후)", "교보생명(후)",
        "삼성화재", "삼성생명", "신한라이프(후)", "흥국생명보험(후)", "동양생명보험(후)", "미래에셋생명(후)"
    ],
    "5대금융지주": [
        "신한지주", "하나금융지주", "KB금융", "농협금융지주", "우리금융지주"
    ],
    "5대시중은행": [
        "농협은행", "국민은행", "신한은행", "우리은행", "하나은행"
    ],
    "카드사": [
        "케이비카드", "현대카드", "신한카드", "비씨카드", "삼성카드"
    ],
    "캐피탈": [
        "한국캐피탈", "현대캐피탈"
    ],
    "지주사": [
        "SK이노베이션", "지에스에너지", "SK", "GS"
    ],
    "에너지": [
        "SK가스", "GS칼텍스", "S-Oil", "SK에너지", "에스케이엔무브", "코리아에너지터미널"
    ],
    "발전": [
        "GS파워", "지에스이피에스", "삼천리"
    ],
    "자동차": [
        "LG에너지솔루션", "한온시스템", "포스코퓨처엠", "한국타이어앤테크놀로지"
    ],
    "전기/전자": [
        "SK하이닉스", "LG이노텍", "LG전자", "엘에스일렉트릭"
    ],
    "소비재": [
        "이마트", "LF", "CJ제일제당", "SK네트웍스", "CJ대한통운"
    ],
    "비철/철강": [
        "포스코", "현대제철", "고려아연"
    ],
    "석유화학": [
        "LG화학", "SK지오센트릭"
    ],
    "건설": [
        "포스코이앤씨"
    ],
    "특수채": [
        "주택도시보증공사", "기업은행"
    ]
}

# --- 공통 필터 옵션(대분류/소분류 없이 모두 적용) ---
common_filter_categories = {
    "신용/등급": [
        "신용등급", "등급전망", "하락", "강등", "하향", "상향", "디폴트", "부실", "부도", "미지급", "수요 미달", "미매각", "제도 개편", "EOD"
    ],
    "수요/공급": [
        "수요", "공급", "수급", "둔화", "위축", "성장", "급등", "급락", "상승", "하락", "부진", "심화"
    ],
    "실적/재무": [
        "실적", "매출", "영업이익", "적자", "손실", "비용", "부채비율", "이자보상배율"
    ],
    "자금/조달": [
        "차입", "조달", "설비투자", "회사채", "발행", "인수", "매각"
    ],
    "구조/조정": [
        "M&A", "합병", "계열 분리", "구조조정", "다각화", "구조 재편"
    ],
    "거시/정책": [
        "금리", "환율", "관세", "무역제재", "보조금", "세액 공제", "경쟁"
    ],
    "지배구조/법": [
        "횡령", "배임", "공정거래", "오너리스크", "대주주", "지배구조"
    ]
}
ALL_COMMON_FILTER_KEYWORDS = []
for keywords in common_filter_categories.values():
    ALL_COMMON_FILTER_KEYWORDS.extend(keywords)

# --- 산업별 필터 옵션 ---
industry_filter_categories = {
    "은행 및 금융지주": [
        "경영실태평가", "BIS", "CET1", "자본비율", "상각형 조건부자본증권", "자본확충", "자본여력", "자본적정성", "LCR",
        "조달금리", "NIM", "순이자마진", "고정이하여신비율", "대손충당금", "충당금", "부실채권", "연체율", "가계대출", "취약차주"
    ],
    "보험사": [
        "보장성보험", "저축성보험", "변액보험", "퇴직연금", "일반보험", "자동차보험", "ALM", "지급여력비율", "K-ICS",
        "보험수익성", "보험손익", "수입보험료", "CSM", "상각", "투자손익", "운용성과", "IFRS4", "IFRS17", "보험부채",
        "장기선도금리", "최종관찰만기", "유동성 프리미엄", "신종자본증권", "후순위채", "위험자산비중", "가중부실자산비율"
    ],
    "카드사": [
        "민간소비지표", "대손준비금", "가계부채", "연체율", "가맹점카드수수료", "대출성자산", "신용판매자산", "고정이하여신", "레버리지배율",
        "건전성", "케이뱅크", "이탈"
    ],
    "캐피탈": [
        "충당금커버리지비율", "고정이하여신", "PF구조조정", "리스자산", "손실흡수능력", "부동산PF연체채권", "자산포트폴리오", "건전성",
        "조정총자산수익률", "군인공제회"
    ],
    "지주사": [
        "SK지오센트릭", "SK에너지", "SK엔무브", "SK인천석유화학", "GS칼텍스", "GS파워", "SK이노베이션", "SK텔레콤", "SK온",
        "GS에너지", "GS리테일", "GS E&C", "2차전지", "석유화학", "윤활유", "전기차", "배터리", "정유", "이동통신"
    ],
    "에너지": [
        "정유", "유가", "정제마진", "스프레드", "가동률", "재고 손실", "중국 수요", "IMO 규제", "저유황 연료", "LNG",
        "터미널", "윤활유"
    ],
    "발전": [
        "LNG", "천연가스", "유가", "SMP", "REC", "계통시장", "탄소세", "탄소배출권", "전력시장 개편", "전력 자율화",
        "가동률", "도시가스"
    ],
    "자동차": [
        "AMPC 보조금", "IRA 인센티브", "중국 배터리", "EV 수요", "전기차", "ESS수요", "리튬", "타이어"
    ],
    "전기전자": [
        "CHIPS 보조금", "중국", "DRAM", "HBM", "광할솔루션", "아이폰", "HVAC", "HVTR"
    ],
    "철강": [
        "철광석", "후판", "강판", "철근", "스프레드", "철강", "가동률", "제철소", "셧다운", "중국산 저가",
        "중국 수출 감소", "건설경기", "조선 수요", "파업"
    ],
    "비철": [
        "연", "아연", "니켈", "안티모니", "경영권 분쟁", "MBK", "영풍"
    ],
    "소매": [
        "내수부진", "시장지배력", "SK텔레콤", "SK매직", "CLS", "HMR", "라이신", "아미노산", "슈완스컴퍼니",
        "의류", "신세계", "대형마트 의무휴업", "G마켓", "W컨셉", "스타필드"
    ],
    "석유화학": [
        "석유화학", "석화", "유가", "증설", "스프레드", "가동률", "PX", "벤젠", "중국 증설", "중동 COTC",
        "LG에너지솔루션", "전기차", "배터리", "리튬", "IRA", "AMPC"
    ],
    "건설": [
        "철근 가격", "시멘트 가격", "공사비", "SOC 예산", "도시정비 지원", "우발채무", "수주", "주간사", "사고",
        "시공능력순위", "미분양", "대손충당금"
    ],
    "특수채": [
        "자본확충", "HUG", "전세사기", "보증사고", "보증료율", "회수율", "보증잔액", "대위변제액",
        "중소기업대출", "대손충당금", "부실채권", "불법", "구속"
    ]
}

# --- 카테고리-산업 대분류 매핑 함수 ---
def get_industry_majors_from_favorites(selected_categories):
    favorite_to_industry_major = {
        "5대금융지주": ["은행 및 금융지주"],
        "5대시중은행": ["은행 및 금융지주"],
        "보험사": ["보험사"],
        "카드사": ["카드사"],
        "캐피탈": ["캐피탈"],
        "지주사": ["지주사"],
        "에너지": ["에너지"],
        "발전": ["발전"],
        "자동차": ["자동차"],
        "석유화학": ["석유화학"],
        "전기/전자": ["전기전자"],
        "비철/철강": ["철강", "비철"],
        "소비재": ["소매"],
        "건설": ["건설"],
        "특수채": ["특수채"],
    }
    majors = set()
    for cat in selected_categories:
        for major in favorite_to_industry_major.get(cat, []):
            majors.add(major)
    return list(majors)

# --- UI 시작 ---
st.set_page_config(layout="wide")
col_title, col_option1, col_option2 = st.columns([0.6, 0.2, 0.2])
with col_title:
    st.markdown(
        "&lt;h1 style='color:#1a1a1a; margin-bottom:0.5rem;'&gt;"
        "&lt;a href='https://credit-issue-monitoring-news-sending.onrender.com/' target='_blank' style='text-decoration:none; color:#1a1a1a;'&gt;"
        "📊 Credit Issue Monitoring&lt;/a&gt;&lt;/h1&gt;",
        unsafe_allow_html=True
    )
with col_option1:
    show_sentiment_badge = st.checkbox("기사목록에 감성분석 배지 표시", value=False, key="show_sentiment_badge")
with col_option2:
    enable_summary = st.checkbox("요약 기능 적용", value=False, key="enable_summary")

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
today = datetime.today().date()
if "end_date" not in st.session_state:
    st.session_state["end_date"] = today
if "start_date" not in st.session_state:
    st.session_state["start_date"] = today - timedelta(days=7)
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
    use_industry_filter = st.checkbox("이 필터 적용", value=True, key="use_industry_filter")
    
    # 세션 변수 초기화
    if "industry_major_sub_map" not in st.session_state:
        st.session_state.industry_major_sub_map = {}

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
    require_exact_keyword_in_title_or_content = st.checkbox("키워드가 제목 또는 본문에 포함된 기사만 보기", value=True, key="require_exact_keyword_in_title_or_content")
    # 중복 기사 제거 체크박스 추가 (기본 해제)
    remove_duplicate_articles = st.checkbox("중복 기사 제거", value=False, key="remove_duplicate_articles", help="키워드 검색 후 중복 기사를 제거합니다.")
    filter_allowed_sources_only = st.checkbox(
    "특정 언론사만 검색", 
    value=True, 
    key="filter_allowed_sources_only", 
    help="선택된 메이저 언론사만 필터링하고, 그 외 언론은 제외합니다."
)

def extract_article_text(url):
    try:
        article = newspaper.Article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        return f"본문 추출 오류: {e}"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def detect_lang(text):
    return "ko" if re.search(r"[가-힣]", text) else "en"

def summarize_and_sentiment_with_openai(text, do_summary=True):
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", None, None, None
    lang = detect_lang(text)
    if lang == "ko":
        prompt = (
            ("아래 기사 본문을 감성분석(긍정/부정만)하고" +
             ("\n- [한 줄 요약]: 기사 전체 내용을 한 문장으로 요약" if do_summary else "") +
             "\n- [감성]: 기사 전체의 감정을 긍정/부정 중 하나로만 답해줘. 중립은 절대 답하지 마. 파산, 자금난 등 부정적 사건이 중심이면 반드시 '부정'으로 답해줘.\n\n"
             "아래 포맷으로 답변해줘:\n" +
             ("[한 줄 요약]: (여기에 한 줄 요약)\n" if do_summary else "") +
             "[감성]: (긍정/부정 중 하나만)\n\n"
             "[기사 본문]\n" + text)
        )
    else:
        prompt = (
            ("Analyze the following news article for sentiment (positive/negative only)." +
             ("\n- [One-line Summary]: Summarize the entire article in one sentence." if do_summary else "") +
             "\n- [Sentiment]: Classify the overall sentiment as either positive or negative ONLY. Never answer 'neutral'. If the article is about bankruptcy, crisis, etc., answer 'negative'.\n\n"
             "Respond in this format:\n" +
             ("[One-line Summary]: (your one-line summary)\n" if do_summary else "") +
             "[Sentiment]: (positive/negative only)\n\n"
             "[ARTICLE]\n" + text)
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
        m3 = re.search(r"\[감성\]:\s*(.+)", answer)
    else:
        m1 = re.search(r"\[One-line Summary\]:\s*(.+)", answer)
        m3 = re.search(r"\[Sentiment\]:\s*(.+)", answer)
    one_line = m1.group(1).strip() if (do_summary and m1) else ""
    summary = ""
    sentiment = m3.group(1).strip() if m3 else ""
    if sentiment.lower() in ['neutral', '중립', '']:
        sentiment = '부정' if lang == "ko" else 'negative'
    if lang == "en":
        sentiment = '긍정' if sentiment.lower() == 'positive' else '부정'
    return one_line, summary, sentiment, text

def infer_source_from_url(url):
    domain = urlparse(url).netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

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
            title, desc = item["title"], item["description"]
            pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z").date()

            if start_date and pub_date < start_date:
                continue
            if end_date and pub_date > end_date:
                continue
            if not filter_by_issues(title, desc, [query], require_keyword_in_title):
                continue
            if exclude_by_title_keywords(re.sub("<.*?>", "", title), EXCLUDE_TITLE_KEYWORDS):
                continue

            source = item.get("source")
            if not source or source.strip() == "":
                source = infer_source_from_url(item.get("originallink", ""))
                if not source:
                    source = "Naver"
            source_domain = source.lower()
            if source_domain.startswith("www."):
                source_domain = source_domain[4:]

            # 언론사 필터링: ALLOWED_SOURCES에 없으면 스킵
            if source_domain not in ALLOWED_SOURCES:
                continue

            articles.append({
                "title": re.sub("<.*?>", "", title),
                "link": item["link"],
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

    # 키워드 필터: 입력 키워드 OR 카테고리 키워드에 포함되어야 함
    all_keywords = []
    if "keyword_input" in st.session_state:
        all_keywords.extend([k.strip() for k in st.session_state["keyword_input"].split(",") if k.strip()])
    if "cat_multi" in st.session_state:
        for cat in st.session_state["cat_multi"]:
            all_keywords.extend(favorite_categories.get(cat, []))

    if not article_contains_exact_keyword(article, all_keywords):
        return False

    # ✅ 언론사 도메인 필터링 (체크박스로 ON/OFF 가능)
    if st.session_state.get("filter_allowed_sources_only", True):
        source = article.get('source', '').lower()
        if source.startswith("www."):
            source = source[4:]
        if source not in ALLOWED_SOURCES:
            return False

    # 공통 키워드 필터 조건 (OR 조건)
    common_passed = or_keyword_filter(article, ALL_COMMON_FILTER_KEYWORDS)

    # 산업 필터 조건 (사용 시)
    industry_passed = True
    if st.session_state.get("use_industry_filter", False):
        keyword = article.get("키워드")
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

    if not (common_passed or industry_passed):
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
        process_keywords_parallel(
            sorted(keyword_list),  # ✅ 오류 발생 안 함
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
        process_keywords_parallel(
            sorted(keywords),  # ✅ 수정: keyword_list → sorted(keywords)
            st.session_state["start_date"],
            st.session_state["end_date"],
            require_keyword_in_title=st.session_state.get("require_exact_keyword_in_title_or_content", False)
        )

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
            title, desc = item["title"], item["description"]
            pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z").date()

            if start_date and pub_date < start_date:
                continue
            if end_date and pub_date > end_date:
                continue
            if not filter_by_issues(title, desc, [query], require_keyword_in_title):
                continue
            if exclude_by_title_keywords(re.sub("<.*?>", "", title), EXCLUDE_TITLE_KEYWORDS):
                continue

            source = item.get("source")
            if not source or source.strip() == "":
                source = infer_source_from_url(item.get("originallink", ""))
                if not source:
                    source = "Naver"
            source_domain = source.lower()
            if source_domain.startswith("www."):
                source_domain = source_domain[4:]

            articles.append({
                "title": re.sub("<.*?>", "", title),
                "link": item["link"],
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": source_domain
            })

        if len(items) < 100:
            break
    return articles[:limit]

def safe_title(val):
    if pd.isnull(val) or str(val).strip() == "" or str(val).lower() == "nan" or str(val) == "0":
        return "제목없음"
    return str(val)

def get_excel_download_with_favorite_and_excel_company_col(summary_data, favorite_categories, excel_company_categories):
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

    # ✅ 보호 로직: '키워드' 컬럼이 없으면 빈 엑셀 반환
    if "키워드" not in df_articles.columns:
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            pd.DataFrame(columns=["기업명", "표기명", "긍정 뉴스", "부정 뉴스"]).to_excel(writer, index=False)
        output.seek(0)
        return output

    result_rows = []
    for idx, company in enumerate(company_order):
        excel_company_name = excel_company_order[idx] if idx < len(excel_company_order) else ""
        comp_articles = df_articles[df_articles["키워드"] == company]
        pos_news = comp_articles[comp_articles["감성"] == "긍정"].sort_values(by="날짜", ascending=False)
        neg_news = comp_articles[comp_articles["감성"] == "부정"].sort_values(by="날짜", ascending=False)

        if not pos_news.empty:
            pos_date = pos_news.iloc[0]["날짜"]
            pos_title = pos_news.iloc[0]["기사제목"]
            pos_link = pos_news.iloc[0]["링크"]
            pos_display = f'({pos_date}) {pos_title}'
            pos_hyperlink = f'=HYPERLINK("{pos_link}", "{pos_display}")'
        else:
            pos_hyperlink = ""

        if not neg_news.empty:
            neg_date = neg_news.iloc[0]["날짜"]
            neg_title = neg_news.iloc[0]["기사제목"]
            neg_link = neg_news.iloc[0]["링크"]
            neg_display = f'({neg_date}) {neg_title}'
            neg_hyperlink = f'=HYPERLINK("{neg_link}", "{neg_display}")'
        else:
            neg_hyperlink = ""

        result_rows.append({
            "기업명": company,
            "표기명": excel_company_name,
            "긍정 뉴스": pos_hyperlink,
            "부정 뉴스": neg_hyperlink
        })

    df_result = pd.DataFrame(result_rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_result.to_excel(writer, index=False, sheet_name='뉴스요약')
    output.seek(0)
    return output

def build_important_excel_same_format(important_articles, favorite_categories, excel_company_categories):
    """
    중요기사 자동 추출 결과를 기존 '맞춤 엑셀 양식'과 동일한 포맷으로 저장
    """
    company_order = []
    excel_company_order = []

    for cat in [
        "국/공채", "공공기관", "보험사", "5대금융지주", "5대시중은행", "카드사", "캐피탈",
        "지주사", "에너지", "발전", "자동차", "전기/전자", "소비재", "비철/철강", "석유화학", "건설", "특수채"
    ]:
        company_order.extend(favorite_categories.get(cat, []))
        excel_company_order.extend(excel_company_categories.get(cat, []))

    # 기업별 기사 정리
    rows = []
    for i, comp in enumerate(company_order):
        display_name = excel_company_order[i] if i < len(excel_company_order) else ""
        pos_article = ""
        neg_article = ""

        # 이 기업에 해당하는 기사들 필터링
        articles = [a for a in important_articles if a["회사명"] == comp]

        for article in articles:
            link = article["링크"]
            title = article["제목"]
            date = article["날짜"]
            display_text = f"({date}) {title}"
            hyperlink = f'=HYPERLINK("{link}", "{display_text}")'

            if article["감성"] == "긍정":
                pos_article = hyperlink
            elif article["감성"] == "부정":
                neg_article = hyperlink

        rows.append({
            "기업명": comp,
            "표기명": display_name,
            "긍정 뉴스": pos_article,
            "부정 뉴스": neg_article
        })

    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="중요뉴스_양식")
    output.seek(0)
    return output

def generate_important_article_list(search_results, common_keywords, industry_keywords, favorites):
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
            prompt = (
                f"[필터 키워드]\n{', '.join(filtered_keywords)}\n\n"
                f"[기사 목록]\n{prompt_list}\n\n"
                "각 기사에 대해 감성(긍정/부정)을 판단하고,\n"
                "제목에 필터 키워드가 포함된 뉴스만 기준으로,\n"
                "- 긍정에서 가장 중요한 뉴스 1건\n"
                "- 부정에서 가장 중요한 뉴스 1건\n"
                "을 골라주세요. 없으면 빈칸으로:\n\n"
                "[긍정]: (뉴스 제목)\n[부정]: (뉴스 제목)"
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0.3
                )
                answer = response.choices[0].message.content.strip()
                import re
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

def extract_keyword_from_link(search_results, article_link):
    for keyword, articles in search_results.items():
        for article in articles:
            if article["link"] == article_link:
                return keyword
    return "알수없음"

def render_important_article_review_and_download():
    with st.container(border=True):
        st.markdown("### ⭐ 중요 기사 리뷰 및 편집")
        
        # 중요기사 자동 선정 버튼
        if st.button("🚀 OpenAI 기반 중요 기사 자동 선정"):
            with st.spinner("OpenAI로 중요 뉴스 선정 중..."):
                important_articles = generate_important_article_list(
                    search_results=st.session_state.search_results,
                    common_keywords=ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords=st.session_state.get("industry_sub", []),
                    favorites=favorite_categories
                )
                st.session_state.important_articles_preview = important_articles
                st.session_state.important_selected_index = []
        
        # 중요기사 리스트 없으면 안내 메시지
        if not st.session_state.get("important_articles_preview"):
            st.info("아직 중요 기사 후보가 없습니다. 위 버튼을 눌러 자동 생성하십시오.")
            return
        
        st.markdown("🎯 **중요 기사 목록** (교체 또는 삭제할 항목을 체크하세요)")
        
        # 중요기사 체크박스 리스트
        new_selection = []
        for idx, article in enumerate(st.session_state["important_articles_preview"]):
            checked = st.checkbox(
                f"{article['회사명']} | {article['감성']} | {article['제목']}",
                key=f"important_chk_{idx}",
                value=(idx in st.session_state.important_selected_index), on_change=None
            )
            if checked:
                new_selection.append(idx)
        st.session_state.important_selected_index = new_selection

        # --- 추가 버튼, 삭제 버튼, 교체 버튼 한 줄에 배치 ---
        col_add, col_del, col_rep = st.columns([0.3, 0.35, 0.35])

        with col_add:
            if st.button("➕ 선택 기사 추가"):
                left_selected_keys = [k for k, v in st.session_state.article_checked_left.items() if v]
                if len(left_selected_keys) != 1:
                    st.warning("왼쪽 뉴스검색 결과에서 기사 1개만 선택해 주세요.")
                else:
                    from_key = left_selected_keys[0]
                    # --- 유니크ID로 기사 탐색 ---
                    m = re.match(r"^[^_]+_[0-9]+_(.+)$", from_key)
                    if not m:
                        st.warning("기사 식별자 파싱 실패")
                        return
                    key_tail = m.group(1)
                    selected_article = None
                    article_link = None
                    for kw, arts in st.session_state.search_results.items():
                        for art in arts:
                            uid = re.sub(r'\W+', '', art['link'])[-16:]
                            if uid == key_tail:
                                selected_article = art
                                article_link = art["link"]
                                break
                        if selected_article: break

                    if not selected_article or not article_link:
                        st.warning("선택한 기사 정보를 찾을 수 없습니다.")
                        return

                    # 🔷 회사명을 항상 extract_keyword_from_link로 찾음!
                    keyword = extract_keyword_from_link(st.session_state.search_results, article_link)

                    # 감성 정보 확인 또는 요약/감성 다시 생성
                    cleaned_id = re.sub(r'\W+', '', selected_article['link'])[-16:]
                    sentiment = None
                    for k in st.session_state.keys():
                        if k.startswith("summary_") and cleaned_id in k:
                            sentiment = st.session_state[k][2]
                            break
                    if not sentiment:
                        _, _, sentiment, _ = summarize_article_from_url(selected_article["link"], selected_article["title"])

                    new_article = {
                        "회사명": keyword,
                        "감성": sentiment or "",
                        "제목": selected_article["title"],
                        "링크": selected_article["link"],
                        "날짜": selected_article["date"],
                        "출처": selected_article["source"]
                    }

                    important = st.session_state.get("important_articles_preview", [])
                    if any(a["링크"] == new_article["링크"] for a in important):
                        st.info("이미 중요 기사 목록에 존재하는 기사입니다.")
                    else:
                        important.append(new_article)
                        st.session_state["important_articles_preview"] = important
                        st.success("중요 기사 목록에 추가되었습니다: " + new_article["제목"])
                        st.rerun()

        with col_del:
            if st.button("🗑 선택 기사 삭제"):
                for idx in sorted(st.session_state.important_selected_index, reverse=True):
                    if 0 <= idx < len(st.session_state["important_articles_preview"]):
                        st.session_state["important_articles_preview"].pop(idx)
                st.session_state.important_selected_index = []
                st.rerun()

        with col_rep:
            if st.button("🔁 선택 기사 교체"):
                left_selected_keys = [k for k, v in st.session_state.article_checked_left.items() if v]
                right_selected_indexes = st.session_state.important_selected_index
                if len(left_selected_keys) != 1 or len(right_selected_indexes) != 1:
                    st.warning("왼쪽에서 기사 1개, 오른쪽에서 기사 1개만 선택해주세요.")
                    return

                from_key = left_selected_keys[0]
                target_idx = right_selected_indexes[0]
                m = re.match(r"^[^_]+_[0-9]+_(.+)$", from_key)
                if not m:
                    st.warning("기사 식별자 파싱 실패")
                    return
                key_tail = m.group(1)
                selected_article = None
                article_link = None
                for kw, art_list in st.session_state.search_results.items():
                    for art in art_list:
                        uid = re.sub(r'\W+', '', art['link'])[-16:]
                        if uid == key_tail:
                            selected_article = art
                            article_link = art["link"]
                            break
                    if selected_article:
                        break

                if not selected_article or not article_link:
                    st.warning("왼쪽에서 선택한 기사 정보를 찾을 수 없습니다.")
                    return

                # 🔷 회사명(키워드)은 extract_keyword_from_link로 정확히 결정
                keyword = extract_keyword_from_link(st.session_state.search_results, article_link)
                cleaned_id = re.sub(r'\W+', '', selected_article['link'])[-16:]
                sentiment = None
                for k in st.session_state.keys():
                    if k.startswith("summary_") and cleaned_id in k:
                        sentiment = st.session_state[k][2]
                        break
                if not sentiment:
                    _, _, sentiment, _ = summarize_article_from_url(selected_article["link"], selected_article["title"])

                new_article = {
                    "회사명": keyword,
                    "감성": sentiment or "",
                    "제목": selected_article["title"],
                    "링크": selected_article["link"],
                    "날짜": selected_article["date"],
                    "출처": selected_article["source"]
                }
                st.session_state["important_articles_preview"][target_idx] = new_article
                st.session_state.article_checked_left[from_key] = False
                st.session_state.article_checked[from_key] = False
                st.session_state.important_selected_index = []
                st.success("중요 기사 교체 완료: " + new_article["제목"])
                st.rerun()

        st.markdown("---")
        st.markdown("📥 **리뷰한 중요 기사들을 엑셀로 다운로드하세요.**")
        output_excel = build_important_excel_same_format(
            st.session_state["important_articles_preview"],
            favorite_categories,
            excel_company_categories
        )
        st.download_button(
            label="📥 중요 기사 최종 엑셀 다운로드 (맞춤 양식)",
            data=output_excel.getvalue(),
            file_name="중요뉴스_최종선정_양식.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

def render_articles_with_single_summary_and_telegram(results, show_limit, show_sentiment_badge=True, enable_summary=True):
    SENTIMENT_CLASS = {
        "긍정": "sentiment-positive",
        "부정": "sentiment-negative"
    }

    if "article_checked" not in st.session_state:
        st.session_state.article_checked = {}

    col_list, col_summary = st.columns([1, 1])

    with col_list:
        st.markdown("### 🔍 뉴스 검색 결과")
        for keyword, articles in results.items():
            with st.container(border=True):
                st.markdown(f"**[{keyword}] ({len(articles)}건)**")

                for idx, article in enumerate(articles):
                    unique_id = re.sub(r'\W+', '', article['link'])[-16:]
                    key = f"{keyword}_{idx}_{unique_id}"
                    cache_key = f"summary_{key}"

                    # 체크박스와 제목 렌더링
                    cols = st.columns([0.04, 0.96])
                    with cols[0]:
                        checked = st.checkbox("", value=st.session_state.article_checked.get(key, False), key=f"news_{key}")
                    with cols[1]:
                        sentiment = ""
                        if show_sentiment_badge and cache_key in st.session_state:
                            _, _, sentiment, _ = st.session_state[cache_key]
                        badge_html = f"<span class='sentiment-badge {SENTIMENT_CLASS.get(sentiment, 'sentiment-negative')}'>({sentiment})</span>" if sentiment else ""
                        st.markdown(f"[{article['title']}]({article['link']}) {badge_html} {article['date']} | {article['source']}", unsafe_allow_html=True)

                    st.session_state.article_checked_left[key] = checked
                    if checked:
                        st.session_state.article_checked[key] = True

    # 선택 기사 요약 및 다운로드
    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):
            selected_articles = []

            for keyword, articles in results.items():
                for idx, article in enumerate(articles):
                    unique_id = re.sub(r'\W+', '', article['link'])[-16:]
                    key = f"{keyword}_{idx}_{unique_id}"
                    cache_key = f"summary_{key}"

                    if st.session_state.article_checked.get(key, False):
                        if cache_key in st.session_state:
                            one_line, summary, sentiment, full_text = st.session_state[cache_key]
                        else:
                            one_line, summary, sentiment, full_text = summarize_article_from_url(
                                article['link'], article['title'], do_summary=enable_summary
                            )
                            st.session_state[cache_key] = (one_line, summary, sentiment, full_text)

                        selected_articles.append({
                            "키워드": keyword,
                            "기사제목": safe_title(article['title']),
                            "요약": one_line,
                            "요약본": summary,
                            "감성": sentiment,
                            "링크": article['link'],
                            "날짜": article['date'],
                            "출처": article['source']
                        })

                        st.markdown(
                            f"#### [{article['title']}]({article['link']}) "
                            f"<span class='sentiment-badge {SENTIMENT_CLASS.get(sentiment, 'sentiment-negative')}'>({sentiment})</span>",
                            unsafe_allow_html=True
                        )
                        st.markdown(f"- **날짜/출처:** {article['date']} | {article['source']}")
                        if enable_summary:
                            st.markdown(f"- **한 줄 요약:** {one_line}")
                        st.markdown(f"- **감성분석:** `{sentiment}`")
                        st.markdown("---")

            st.session_state.selected_articles = selected_articles
            st.write(f"선택된 기사 개수: {len(selected_articles)}")

            col_dl1, col_dl2 = st.columns([0.5, 0.5])
            with col_dl1:
                st.download_button(
                    label="📥 맞춤 엑셀 다운로드",
                    data=get_excel_download_with_favorite_and_excel_company_col(
                        st.session_state.selected_articles,
                        favorite_categories,
                        excel_company_categories
                    ).getvalue(),
                    file_name="뉴스요약_맞춤형.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # 중요 기사 리뷰 UI
        render_important_article_review_and_download()

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
