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
import pandas as pd
from io import BytesIO
import requests
import re
import os
from datetime import datetime
import telepot
from openai import OpenAI
import newspaper  # newspaper3k

# --- CSS: 체크박스와 기사 사이 gap 최소화 및 감성 뱃지 스타일, flex row 버튼 하단정렬 ---
st.markdown("""
<style>
[data-testid="column"] > div {
    gap: 0rem !important;
}
.stMultiSelect [data-baseweb="tag"] {
    background-color: #ff5c5c !important;
    color: white !important;
    border: none !important;
    font-weight: bold;
}
.sentiment-badge {
    display: inline-block;
    padding: 0.08em 0.6em;
    margin-left: 0.2em;
    border-radius: 0.8em;
    font-size: 0.85em;
    font-weight: bold;
    vertical-align: middle;
}
.sentiment-positive { background: #2ecc40; color: #fff; }
.sentiment-negative { background: #ff4136; color: #fff; }
.stBox {
    background: #fcfcfc;
    border-radius: 0.7em;
    border: 1.5px solid #e0e2e6;
    margin-bottom: 1.2em;
    padding: 1.1em 1.2em 1.2em 1.2em;
    box-shadow: 0 2px 8px 0 rgba(0,0,0,0.03);
}
.flex-row-top {
    display: flex;
    align-items: flex-start;
    gap: 1.2rem;
    margin-bottom: 0.5rem;
}
.flex-grow {
    flex: 1 1 0%;
}
.flex-btn {
    min-width: 90px;
}
</style>
""", unsafe_allow_html=True)

# ----------------- 제외 키워드(제목에 포함시 해당 기사 제외) -----------------
EXCLUDE_TITLE_KEYWORDS = [
    "야구", "축구", "배구", "농구", "골프", "e스포츠", "올림픽", "월드컵", "K리그", "프로야구", "프로축구", "프로배구", "프로농구",
    "부고", "인사", "승진", "임명", "발령", "인사발령", "인사이동",
    "브랜드평판", "브랜드 평판", "브랜드 순위", "브랜드지수",
    "코스피", "코스닥", "주가", "주식", "증시", "시세", "마감", "장중", "장마감", "거래량", "거래대금", "상한가", "하한가"
]

def exclude_by_title_keywords(title, exclude_keywords):
    for word in exclude_keywords:
        if word in title:
            return True
    return False

# ----------------- 세션 상태 변수 초기화 -----------------
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

company_filter_categories = {
    "현대해상": [],
    "농협생명": [],
    "메리츠화재": ["부동산PF"],
    "교보생명": [],
    "삼성화재": [],
    "삼성생명": [],
    "신한라이프생명보험": [],
    "흥국생명보험": ["태광그룹"],
    "동양생명": ["다자보험", "안방그룹", "우리금융"],
    "미래에셋생명": [],
    "KB국민카드": [],
    "현대카드": ["PLCC", "카드대출자산 취급확대"],
    "신한카드": [],
    "비씨카드": ["회원사 이탈", "IPO", "케이뱅크"],
    "삼성카드": [],
    "한국캐피탈": ["군인공제회"],
    "현대캐피탈": ["자동차금융"],
    "SK이노베이션": ["SK지오센트릭", "SK에너지", "SK앤무브", "SK인천석유화학", "2차전지", "석유화학", "윤활유", "전기차", "배터리"],
    "GS에너지": ["GS칼텍스", "GS파워", "정유", "열병합 수요"],
    "SK": ["SK이노베이션", "SK텔레콤", "SK온", "배터리", "석유화학", "이동통신"],
    "GS": ["GS에너지", "GS리테일", "GS E&C", "정유", "건설", "유통"],
    "SK가스": ["프로필렌", "LPG 파생상품", "터미널"],
    "GS칼텍스": ["GS에너지", "PX스프레드", "윤활기유", "저탄소 산업"],
    "S-Oil": ["PX스프레드", "윤활기유", "Sheheen", "saudi aramco"],
    "SK에너지": [],
    "SK앤무브": ["SK이노베이션", "윤활유", "기유 스프레드", "미전환유", "액침냉각"],
    "코리아에너지터미널": ["터미널", "가동률", "LNG 터미널 수요", "에너지 전환 정책"],
    "GS파워": ["GS", "가동률", "증설", "열병합 수요"],
    "GSEPS": ["GS", "가동률", "바이오매스"],
    "삼천리": ["도시가스", "계열 분리", "KOGAS 조달단가"],
    "LG에너지솔루션": ["중국산 배터리 규제", "리튬"],
    "한온시스템": ["한앤컴퍼니", "HVAC", "탄소중립정책"],
    "포스코퓨처엠": ["리튬", "양극재", "음극재"],
    "한국타이어": ["EV 타이어", "전기차 타이어", "합성고무 가격"],
    "SK하이닉스": ["DRAM", "HBM"],
    "LG이노텍": ["스마트폰 판매", "아이폰 판매", "스마트폰", "아이폰", "광학솔루션", "중국 카메라 모듈", "ToF카메라"],
    "LG전자": ["보편관세", "TV 수요", "LCD 가격", "전장 수주잔고", "HVAC", "SCFI컨테이너 지수"],
    "LS일렉트릭": ["HVTR수요", "미국 전력 수요", "증설", "PLC 경쟁"],
    "이마트": ["신세계", "대형마트 의무휴업", "신세계건설", "G마켓", "W컨셉", "스타필드"],
    "LF": ["의류시장", "코람코자산신탁"],
    "CJ제일제당": ["HMR", "라이신", "아미노산", "슈완스컴퍼니"],
    "SK네트웍스": ["SK텔레콤", "SK매직"],
    "CJ대한통운": ["쿠팡", "CLS", "주 7일 배송"],
    "포스코": [],
    "현대제철": ["노사갈등"],
    "고려아연": ["연", "아연", "니켈", "안티모니", "제련", "경영권 분쟁", "MBK", "영풍", "중국 아연 감산", "중국 수출 규제", "재고평가손익"],
    "LG화학": ["LG에너지솔루션", "전기차", "배터리", "북미 점유율", "유럽 배터리 시장", "리튬", "IRA", "AMPC", "EV 수요", "ESS 수요"],
    "SK지오센트릭": ["SK이노베이션"],
    "포스코이앤씨": ["신안산선"],
    "주택도시보증공사(신종)": ["HUG", "전세사기", "보증사고", "보증료율", "회수율", "보증잔액", "대위변제액"],
    "기업은행(후)": ["중소기업대출", "공공기관 해제", "대손충당금", "부실채권", "불법", "구속"]
}
company_major_categories = list(company_filter_categories.keys())
company_sub_categories = {cat: company_filter_categories[cat] for cat in company_major_categories}

# --- 산업별 필터 옵션: 대분류/소분류 키워드 최신화 ---
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
        "민간소비지표", "대손준비금", "가계부채", "연체율", "가맹점카드수수료", "대출성자산", "신용판매자산", "고정이하여신", "레버리지배율", "건전성"
    ],
    "캐피탈": [
        "충당금커버리지비율", "고정이하여신", "PF구조조정", "리스자산", "손실흡수능력", "부동산PF연체채권", "자산포트폴리오", "건전성", "조정총자산수익률"
    ],
    "지주사": [],
    "에너지": [
        "정유", "유가", "정제마진", "스프레드", "가동률", "재고 손실", "중국 수요", "IMO 규제", "저유황 연료", "LNG"
    ],
    "발전": [
        "LNG", "천연가스", "유가", "SMP", "REC", "계통시장", "탄소세", "탄소배출권", "전력시장 개편", "전력 자율화", "한파", "기온 상승"
    ],
    "자동차": [
        "AMPC 보조금", "AMPC", "IRA", "IRA 인센티브", "중국 배터리", "EV 수요", "EV", "전기차", "ESS수요"
    ],
    "전기전자": [
        "CHIPS 보조금", "CHIPS", "중국", "관세"
    ],
    "철강": [
        "철광석", "후판", "강판", "철근", "스프레드", "철강", "가동률", "제철소", "셧다운", "중국산 저가", "중국 수출 감소", "건설경기", "조선 수요", "파업"
    ],
    "비철": [],
    "소매": [
        "내수부진", "시장지배력"
    ],
    "석유화학": [
        "석유화학", "석화", "유가", "증설", "스프레드", "가동률", "PX", "벤젠", "중국 증설", "중동 COTC"
    ],
    "건설": [
        "철근 가격", "시멘트 가격", "공사비", "SOC 예산", "도시정비 지원", "우발채무", "수주", "주간사", "사고", "시공능력순위", "미분양", "대손충당금"
    ],
    "특수채": ["자본확충"]
}
major_categories = list(industry_filter_categories.keys())
sub_categories = {cat: industry_filter_categories[cat] for cat in major_categories}

all_fav_keywords = sorted(set(
    kw for cat in favorite_categories.values() for kw in cat if kw not in ["테스트1", "테스트2", "테스트3"]
))

# --- [공통 필터 옵션] ---
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
common_major_categories = list(common_filter_categories.keys())
common_sub_categories = {cat: common_filter_categories[cat] for cat in common_major_categories}

st.set_page_config(layout="wide")

# --- 상단 컨트롤 한 줄에 정렬 ---
with st.container():
    st.markdown('<div class="flex-row-top">', unsafe_allow_html=True)
    st.markdown('<div class="flex-grow">', unsafe_allow_html=True)
    keywords_input = st.text_input("키워드 (예: 삼성, 한화)", value="", key="keyword_input", label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div>', unsafe_allow_html=True)
    search_clicked = st.button("검색", key="search_btn", help="키워드로 검색")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div style="margin-top:2px;">', unsafe_allow_html=True)
    show_sentiment_badge = st.checkbox("기사목록에 감성분석 배지 표시", value=False, key="show_sentiment_badge")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div style="margin-top:2px;">', unsafe_allow_html=True)
    enable_summary = st.checkbox("요약 기능 적용", value=True, key="enable_summary")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# 2. 즐겨찾기 카테고리 선택/검색 버튼 (한 줄, 버튼 오른쪽)
st.markdown("**⭐ 즐겨찾기 카테고리 선택**")
col_cat_input, col_cat_btn = st.columns([0.8, 0.2])
with col_cat_input:
    selected_categories = st.multiselect("카테고리 선택 시 자동으로 즐겨찾기 키워드에 반영됩니다.", list(favorite_categories.keys()), key="cat_multi")
with col_cat_btn:
    category_search_clicked = st.button("🔍 검색", key="cat_search_btn", help="카테고리로 검색", use_container_width=True)
for cat in selected_categories:
    st.session_state.favorite_keywords.update(favorite_categories[cat])

# 날짜 입력
date_col1, date_col2 = st.columns([1, 1])
with date_col1:
    start_date = st.date_input("시작일")
with date_col2:
    end_date = st.date_input("종료일")

# --- 공통 필터 옵션 (복수 선택, 산업별과 동일 구조) ---
with st.expander("🧩 공통 필터 옵션"):
    use_common_filter = st.checkbox("이 필터 적용", value=False, key="use_common_filter")
    col_common_major, col_common_sub = st.columns([1, 1])
    with col_common_major:
        selected_common_majors = st.multiselect(
            "공통 대분류(분류)",
            common_major_categories,
            key="common_majors"
        )
    sub_options = []
    for major in selected_common_majors:
        sub_options.extend(common_sub_categories.get(major, []))
    sub_options = sorted(set(sub_options))
    with col_common_sub:
        selected_common_sub = st.multiselect(
            "공통 소분류(필터 키워드)",
            sub_options,
            default=sub_options,
            key="common_sub"
        )

# --- 기업별 필터 옵션 (복수 선택, 소분류도 멀티셀렉트) ---
with st.expander("🏢 기업별 필터 옵션"):
    use_company_filter = st.checkbox("이 필터 적용", value=False, key="use_company_filter")
    col_company_major, col_company_sub = st.columns([1, 1])
    with col_company_major:
        selected_companies = st.multiselect("기업명(복수 선택 가능)", company_major_categories, key="company_majors")
    sub_options = []
    for comp in selected_companies:
        sub_options.extend(company_sub_categories.get(comp, []))
    sub_options = sorted(set(sub_options))
    with col_company_sub:
        selected_company_sub = st.multiselect("필터 키워드", sub_options, default=sub_options, key="company_sub")

# --- 산업별 필터 옵션 (이름 옆 체크박스, 대분류/소분류 모두 복수 선택) ---
with st.expander("🏭 산업별 필터 옵션"):
    use_industry_filter = st.checkbox("이 필터 적용", value=False, key="use_industry_filter")
    col_major, col_sub = st.columns([1, 1])
    with col_major:
        selected_majors = st.multiselect(
            "대분류(산업)",
            major_categories,
            key="industry_majors"
        )
    sub_options = []
    for major in selected_majors:
        sub_options.extend(sub_categories.get(major, []))
    sub_options = sorted(set(sub_options))
    with col_sub:
        selected_sub = st.multiselect(
            "소분류(필터 키워드)",
            sub_options,
            default=sub_options,
            key="industry_sub"
        )

# --- 키워드 필터 옵션 (하단으로 이동) ---
with st.expander("🔍 키워드 필터 옵션"):
    require_keyword_in_title = st.checkbox("기사 제목에 키워드가 포함된 경우만 보기", value=False, key="require_keyword_in_title")
    require_exact_keyword_in_title_or_content = st.checkbox("키워드가 온전히 제목 또는 본문에 포함된 기사만 보기", value=False, key="require_exact_keyword_in_title_or_content")

# 이하 뉴스 수집, 요약, 감성분석, 필터, 엑셀다운로드 등은 기존과 동일하게 이어집니다.
# --- 본문 추출 함수, OpenAI 요약/감성분석, 뉴스 API, 기사 필터링 등 기존 코드 그대로 사용 ---

# 기사 요약 결과: 각 키워드별로 접기/펼치기(expander) 적용
def render_articles_with_single_summary_and_telegram(results, show_limit, show_sentiment_badge=True, enable_summary=True):
    SENTIMENT_CLASS = {
        "긍정": "sentiment-positive",
        "부정": "sentiment-negative"
    }

    if "article_checked" not in st.session_state:
        st.session_state.article_checked = {}

    col_list, col_summary = st.columns([1, 1])

    with col_list:
        st.markdown("### 기사 요약 결과")
        for keyword, articles in results.items():
            with st.expander(f"[{keyword}] 기사 요약 결과", expanded=False):
                limit = st.session_state.show_limit.get(keyword, 5)
                for idx, article in enumerate(articles[:limit]):
                    unique_id = re.sub(r'\W+', '', article['link'])[-16:]
                    key = f"{keyword}_{idx}_{unique_id}"
                    cache_key = f"summary_{key}"
                    if show_sentiment_badge:
                        if cache_key not in st.session_state:
                            one_line, summary, sentiment, full_text = summarize_article_from_url(
                                article['link'], article['title'], do_summary=enable_summary
                            )
                            st.session_state[cache_key] = (one_line, summary, sentiment, full_text)
                        else:
                            one_line, summary, sentiment, full_text = st.session_state[cache_key]
                        sentiment_label = sentiment if sentiment else "분석중"
                        sentiment_class = SENTIMENT_CLASS.get(sentiment_label, "sentiment-negative")
                        md_line = (
                            f"[{article['title']}]({article['link']}) "
                            f"<span class='sentiment-badge {sentiment_class}'>({sentiment_label})</span> "
                            f"{article['date']} | {article['source']}"
                        )
                    else:
                        md_line = (
                            f"[{article['title']}]({article['link']}) "
                            f"{article['date']} | {article['source']}"
                        )
                    cols = st.columns([0.04, 0.96])
                    with cols[0]:
                        checked = st.checkbox("", value=st.session_state.article_checked.get(key, False), key=f"news_{key}")
                    with cols[1]:
                        st.markdown(md_line, unsafe_allow_html=True)
                    st.session_state.article_checked[key] = checked

                if limit < len(articles):
                    if st.button("더보기", key=f"more_{keyword}"):
                        st.session_state.show_limit[keyword] += 10
                        st.rerun()

    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):
            selected_articles = []
            def safe_title_for_append(val):
                if val is None or str(val).strip() == "" or str(val).lower() == "nan" or str(val) == "0":
                    return "제목없음"
                return str(val)
            for keyword, articles in results.items():
                limit = st.session_state.show_limit.get(keyword, 5)
                for idx, article in enumerate(articles[:limit]):
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
                            "기사제목": safe_title_for_append(article.get('title')),
                            "요약": one_line,
                            "요약본": summary,
                            "감성": sentiment,
                            "링크": article['link'],
                            "날짜": article['date'],
                            "출처": article['source']
                        })
                        if show_sentiment_badge:
                            st.markdown(
                                f"#### [{article['title']}]({article['link']}) "
                                f"<span class='sentiment-badge {SENTIMENT_CLASS.get(sentiment, 'sentiment-negative')}'>({sentiment})</span>",
                                unsafe_allow_html=True
                            )
                        else:
                            st.markdown(f"#### [{article['title']}]({article['link']})", unsafe_allow_html=True)
                        st.markdown(f"- **날짜/출처:** {article['date']} | {article['source']}")
                        if enable_summary:
                            st.markdown(f"- **한 줄 요약:** {one_line}")
                        st.markdown(f"- **감성분석:** `{sentiment}`")
                        st.markdown("---")

            st.session_state.selected_articles = selected_articles
            st.write(f"선택된 기사 개수: {len(selected_articles)}")

            company_order = []
            for cat in ["보험사", "5대금융지주", "5대시중은행", "카드사", "캐피탈", "지주사", "에너지", "발전", "자동차", "전기/전자", "소비재", "비철/철강", "석유화학", "건설", "특수채"]:
                company_order.extend(favorite_categories.get(cat, []))

            if st.session_state.selected_articles:
                excel_bytes = get_excel_download_custom_with_company_col(st.session_state.selected_articles, company_order)
                st.download_button(
                    label="📥 맞춤 엑셀 다운로드",
                    data=excel_bytes.getvalue(),
                    file_name="뉴스요약_맞춤형.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

if st.session_state.search_results:
    filtered_results = {}
    for keyword, articles in st.session_state.search_results.items():
        filtered_articles = [a for a in articles if article_passes_all_filters(a)]
        if filtered_articles:
            filtered_results[keyword] = filtered_articles
    render_articles_with_single_summary_and_telegram(
        filtered_results,
        st.session_state.show_limit,
        show_sentiment_badge=st.session_state.get("show_sentiment_badge", False),
        enable_summary=st.session_state.get("enable_summary", True)
    )
