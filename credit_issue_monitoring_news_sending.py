import os
import streamlit as st
import pandas as pd
from io import BytesIO
import requests
import re
from datetime import datetime
import telepot
from openai import OpenAI
import newspaper  # newspaper3k
from difflib import SequenceMatcher

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

# --- 중복 기사 제거 함수 ---
def normalize_title(title):
    title = re.sub(r"[^\w\s]", "", title)  # 특수문자 제거
    title = re.sub(r"\s+", " ", title)     # 다중 공백 → 1개
    return title.strip().lower()

def is_similar_title(title1, title2, threshold=0.75):
    t1 = normalize_title(title1)
    t2 = normalize_title(title2)
    ratio = SequenceMatcher(None, t1, t2).ratio()
    return ratio > threshold

def remove_duplicate_articles(articles):
    seen = set()
    unique_articles = []
    for article in articles:
        link = article.get("link")
        if link and link not in seen:
            unique_articles.append(article)
            seen.add(link)
    return unique_articles

def remove_duplicate_articles_by_title(articles, threshold=0.75):
    unique_articles = []
    for article in articles:
        title = article.get("title", "")
        if not any(is_similar_title(title, a.get("title", ""), threshold) for a in unique_articles):
            unique_articles.append(article)
    return unique_articles

def deduplicate_articles(articles, title_threshold=0.75):
    articles = remove_duplicate_articles(articles)
    articles = remove_duplicate_articles_by_title(articles, threshold=title_threshold)
    return articles

# --- 이하 기존 코드와 동일 ---
# (fetch_naver_news, fetch_gnews_news, 기타 함수들은 그대로 두시면 됩니다)
# 아래와 같이 중복 제거 함수가 적용된 부분만 바꿔주시면 됩니다.

# ... (중략: 기존 코드 동일)

def process_keywords(keyword_list, start_date, end_date, require_keyword_in_title=False):
    for k in keyword_list:
        if is_english(k):
            articles = fetch_gnews_news(k, start_date, end_date, require_keyword_in_title=require_keyword_in_title)
        else:
            articles = fetch_naver_news(k, start_date, end_date, require_keyword_in_title=require_keyword_in_title)
        # 링크+제목유사도 중복 제거
        articles = deduplicate_articles(articles, title_threshold=0.75)
        st.session_state.search_results[k] = articles
        if k not in st.session_state.show_limit:
            st.session_state.show_limit[k] = 5

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
            # 링크+제목유사도 중복 제거 (렌더링 직전)
            articles = deduplicate_articles(articles, title_threshold=0.75)
            with st.container(border=True):
                st.markdown(f"**[{keyword}]**")
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
                articles = remove_duplicate_articles(articles)
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

            excel_company_order = []
            for cat in ["국/공채", "공공기관", "보험사", "5대금융지주", "5대시중은행", "카드사", "캐피탈", "지주사", "에너지", "발전", "자동차", "전기/전자", "소비재", "비철/철강", "석유화학", "건설", "특수채"]:
                excel_company_order.extend(excel_company_categories.get(cat, []))

            if st.session_state.selected_articles:
                excel_bytes = get_excel_download_with_favorite_and_excel_company_col(
                    st.session_state.selected_articles,
                    favorite_categories,
                    excel_company_categories
                )
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
