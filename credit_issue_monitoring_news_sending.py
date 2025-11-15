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
from bs4 import BeautifulSoup
import pandas as pd

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
kiscd_map = config.get("kiscd_map", {})
kr_compcd_map = config.get("kr_COMP_CD_map", {})

# 공통 필터 키워드 전체 리스트 생성
ALL_COMMON_FILTER_KEYWORDS = []
for keywords in common_filter_categories.values():
    ALL_COMMON_FILTER_KEYWORDS.extend(keywords)

def extract_file_url(js_href: str) -> str:
    if not js_href or not js_href.startswith("javascript:fn_file"):
        return ""
    m = re.search(r"fn_file\((.*)\)", js_href)
    if not m:
        return ""
    args_str = m.group(1)
    args = [arg.strip().strip("'\"") for arg in args_str.split(",")]
    if len(args) < 4:
        return ""
    file_name = args[3]
    return f"https://www.kisrating.com/common/download.do?filename={file_name}"

def extract_reports_and_research(html: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    result = {
        "평가리포트": [],
        "관련리서치": [],
        "신용등급상세": []
    }

    # 평가리포트, 관련리서치 테이블 로직 그대로
    tables = soup.select('div.table_ty1 > table')
    for table in tables:
        caption = table.find('caption')
        if not caption:
            continue
        caption_text = caption.text.strip()

        if caption_text == "평가리포트":
            rows = table.select('tbody > tr')
            for tr in rows:
                tds = tr.find_all('td')
                if len(tds) < 4:
                    continue
                report_type = tds[0].text.strip()
                a_tag = tds[1].find('a')
                title = a_tag.text.strip() if a_tag else ''
                date = tds[2].text.strip()
                eval_type = tds[3].text.strip()
                result["평가리포트"].append({
                    "종류": report_type,
                    "리포트": title,
                    "일자": date,
                    "평가종류": eval_type
                })
        elif caption_text == "관련 리서치":
            rows = table.select('tbody > tr')
            for tr in rows:
                tds = tr.find_all('td')
                if len(tds) < 4:
                    continue
                category = tds[0].text.strip()
                a_tag = tds[1].find('a')
                title = a_tag.text.strip() if a_tag else ''
                date = tds[2].text.strip()
                result["관련리서치"].append({
                    "구분": category,
                    "제목": title,
                    "일자": date
                })

    # 신용등급상세 추가 (ex. 현대해상 등급 테이블)
    # 기존 extract_credit_details 코드를 활용하여 리스트를 추가
    result["신용등급상세"] = extract_credit_details(html)

    return result

# 별도 함수로 신용등급상세 추출
def extract_credit_details(html):
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    items = soup.select('div.list li')
    for item in items:
        key_tag = item.find('dt') or item.find('strong')
        kind = key_tag.get_text(strip=True) if key_tag else None
        if not kind:
            continue
        # 등급
        grade_tag = item.find('span', string='등급')
        grade_val = ""
        if grade_tag:
            grade_node = grade_tag.find_next(['a', 'strong'])
            grade_val = grade_node.get_text(strip=True) if grade_node else ""
        # Outlook/Watchlist
        outlook_tag = item.find('span', string=lambda s: s and ('Outlook' in s or 'Watchlist' in s))
        outlook_val = outlook_tag.next_sibling.strip() if outlook_tag and outlook_tag.next_sibling else ""
        # 평가일
        eval_date_tag = item.find('span', string='평가일')
        eval_date_val = eval_date_tag.next_sibling.strip() if eval_date_tag and eval_date_tag.next_sibling else ""
        # 평가의견
        eval_opinion_tag = item.find('span', string='평가의견')
        eval_opinion_val = ""
        if eval_opinion_tag:
            next_node = eval_opinion_tag.find_next('a')
            if next_node:
                eval_opinion_val = next_node.get_text(strip=True)
            else:
                eval_opinion_val = eval_opinion_tag.find_next(string=True).strip()
        results.append({
            "종류": kind,
            "등급": grade_val,
            "Outlook/Watchlist": outlook_val,
            "평가일": eval_date_val,
            "평가의견": eval_opinion_val
        })
    return results

def fetch_and_display_reports(companies_map):
    import pandas as pd
    import requests
    import time
    from bs4 import BeautifulSoup

    def extract_table_after_marker(soup, marker_str):
        marker = None
        for tag in soup.find_all(['b', 'strong', 'h2', 'h3', 'span']):
            if marker_str in tag.get_text():
                marker = tag
                break
        return marker.find_next('table') if marker else None

    def parse_grade_table_html(table_tag):
        try:
            dfs = pd.read_html(str(table_tag), header=[0, 1])
            df = dfs[0]
            df.columns = [
                '_'.join([str(l) for l in col if str(l) not in ['nan', 'None']]).strip()
                for col in df.columns.values
            ]
            if all(('Unnamed' in col or col == '' or col.lower() == 'none') for col in df.columns):
                raise Exception("헤더 파싱 실패 - 단일라인 헤더 시도")
            return df
        except Exception:
            try:
                dfs = pd.read_html(str(table_tag), header=0)
                df = dfs[0]
                df.columns = [str(col).strip() for col in df.columns]
                return df
            except Exception:
                try:
                    rows = [
                        [cell.get_text(strip=True) for cell in row.find_all(['th', 'td'])]
                        for row in table_tag.find_all('tr')
                    ]
                    df = pd.DataFrame(rows[1:], columns=rows[0])
                    return df
                except Exception:
                    return pd.DataFrame()

    def table_to_list(table):
        rows = []
        if not table:
            return rows
        for row in table.find_all('tr'):
            cells = [cell.get_text(strip=True) for cell in row.find_all(['th', 'td'])]
            if cells:
                rows.append(cells)
        return rows

    def fetch_nice_rating_data(cmpCd):
        if not cmpCd:
            return {"major_grade_df": pd.DataFrame(), "special_reports": []}
        url = f"https://www.nicerating.com/disclosure/companyGradeInfo.do?cmpCd={cmpCd}"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            major_grade_table_tag = extract_table_after_marker(soup, '주요 등급내역')
            special_report_table_tag = extract_table_after_marker(soup, '스페셜 리포트')
            major_grade_df = parse_grade_table_html(major_grade_table_tag) if major_grade_table_tag else pd.DataFrame()
            special_reports = table_to_list(special_report_table_tag) if special_report_table_tag else []
            return {
                "major_grade_df": major_grade_df,
                "special_reports": special_reports,
            }
        except Exception as e:
            return {
                "major_grade_df": pd.DataFrame(),
                "special_reports": [],
                "error": f"나이스 신용평가 데이터 로드 오류: {e}"
            }

    st.markdown("---")
    st.markdown("### 📑 신용평가 보고서 및 관련 리서치")

    for cat in favorite_categories:
        for company in favorite_categories[cat]:
            kiscd = companies_map.get(company, "")
            cmpcd = config.get("cmpCD_map", {}).get(company, "")
            kr_compcd = kr_compcd_map.get(company, "")
            if not kiscd or not str(kiscd).strip():
                continue

            url_kis = f"https://www.kisrating.com/ratingsSearch/corp_overview.do?kiscd={kiscd}"
            url_nice = f"https://www.nicerating.com/disclosure/companyGradeInfo.do?cmpCd={cmpcd}"
            url_kie = f"https://www.korearatings.com/cms/frDisclosureCon/compView.do?MENU_ID=90&CONTENTS_NO=1&COMP_CD={kr_compcd}"
            with st.expander(
                f"{company} (KISCD: {kiscd} | CMP_CD: {cmpcd} | KIE_CD: {kr_compcd})", expanded=False
            ):
                st.markdown(
                    f"- [한국신용평가 (KIS)]({url_kis}) &nbsp;&nbsp; "
                    f"[나이스신용평가 (NICE)]({url_nice}) &nbsp;&nbsp; "
                    f"[한국기업평가 (KIE)]({url_kie})",
                    unsafe_allow_html=True
                )
                try:
                    resp = requests.get(url_kis, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        html = resp.text
                        report_data = extract_reports_and_research(html)

                        if report_data.get("평가리포트"):
                            with st.expander("평가리포트", expanded=True):
                                st.markdown("### 한국신용평가 평가리포트")
                                df_report = pd.DataFrame(report_data["평가리포트"])
                                df_report = df_report.drop(columns=["다운로드"], errors="ignore")
                                st.dataframe(df_report)

                        if report_data.get("관련리서치"):
                            with st.expander("관련리서치", expanded=True):
                                st.markdown("### 한국신용평가 관련 리서치")
                                df_research = pd.DataFrame(report_data["관련리서치"])
                                df_research = df_research.drop(columns=["다운로드"], errors="ignore")
                                st.dataframe(df_research)

                                nice_data = fetch_nice_rating_data(cmpcd)
                                special_reports = nice_data.get("special_reports", [])
                                st.markdown("#### 나이스 신용평가 스페셜 리포트")
                                if special_reports and len(special_reports) > 1:
                                    header = special_reports[0]
                                    filtered_rows = [row for row in special_reports[1:] if len(row) == len(header)]
                                    if filtered_rows:
                                        df_special = pd.DataFrame(filtered_rows, columns=header)
                                        st.dataframe(df_special)
                                    else:
                                        st.info("표 형식이 맞는 데이터가 없습니다. (스페셜 리포트)")
                                else:
                                    st.info("스페셜 리포트 데이터가 없습니다.")
                                if nice_data.get("error"):
                                    st.warning(nice_data["error"])

                        credit_detail_list = extract_credit_details(html)
                        with st.expander("신용등급 상세정보", expanded=True):
                            if credit_detail_list:
                                st.markdown("### 한국신용평가 신용등급 상세정보")
                                df_credit_detail = pd.DataFrame(credit_detail_list)
                                st.dataframe(df_credit_detail)
                            else:
                                st.info("신용등급 상세정보가 없습니다.")

                            st.markdown("#### 나이스 신용평가 주요 등급내역")
                            nice_data = fetch_nice_rating_data(cmpcd)
                            major_grade_df = nice_data.get("major_grade_df", pd.DataFrame())
                            if not major_grade_df.empty:
                                st.dataframe(major_grade_df)
                            else:
                                st.info("주요 등급내역 데이터가 없습니다.")
                            if nice_data.get("error"):
                                st.warning(nice_data["error"])

                    else:
                        st.warning("한국신용평가 정보를 불러올 수 없습니다.")
                except Exception as e:
                    st.warning(f"신용평가 정보 파싱 오류: {e}")

                time.sleep(1)
            
def expand_keywords_with_synonyms(original_keywords):
    expanded_map = {}
    for kw in original_keywords:
        synonyms = SYNONYM_MAP.get(kw, [])
        expanded_map[kw] = [kw] + synonyms
    return expanded_map

def process_keywords_with_synonyms(favorite_to_expand_map, start_date, end_date, require_keyword_in_title=False):
    """ 
    require_keyword_in_title=True 인 경우:
      1차: 강한 필터(제목/본문에 키워드 포함)로 검색
      → 결과(중복 제거 후)가 0건이면
      2차: 같은 키워드 세트로 require_keyword_in_title=False로 재검색
          이때 가져온 기사에는 relaxed_keyword_match=True 플래그를 달아서,
          후단 필터(article_passes_all_filters)에서 '강제 키워드 포함' 조건에서 제외한다.
    """

    def run_search_for_kw_list(kw_list, start_date, end_date, require_flag, relaxed_flag=False):
        all_articles_local = []
        with ThreadPoolExecutor(max_workers=min(5, len(kw_list))) as executor:
            futures = {
                executor.submit(
                    fetch_naver_news,
                    search_kw,
                    start_date,
                    end_date,
                    require_keyword_in_title=require_flag
                ): search_kw
                for search_kw in kw_list
            }
            for future in as_completed(futures):
                search_kw = futures[future]
                try:
                    fetched = future.result()
                    # 각 기사에 검색어/완화 플래그 추가
                    for a in fetched:
                        a["검색어"] = search_kw
                        if relaxed_flag:
                            a["relaxed_keyword_match"] = True
                    all_articles_local.extend(fetched)
                except Exception as e:
                    st.warning(f"{search_kw} 검색 실패: {e}")
        # 중복 제거 옵션
        if st.session_state.get("remove_duplicate_articles", False):
            all_articles_local = remove_duplicates(all_articles_local)
        return all_articles_local

    for main_kw, kw_list in favorite_to_expand_map.items():
        # 1차: 현재 체크박스 값(require_keyword_in_title) 그대로 적용
        articles = run_search_for_kw_list(
            kw_list,
            start_date,
            end_date,
            require_flag=require_keyword_in_title,
            relaxed_flag=False,
        )

        # 2차 Fallback:
        # - 강력 필터(require_keyword_in_title=True) 상태이고
        # - 1차 검색(중복 제거까지 적용 후) 결과가 0건인 경우에만
        #   → 해당 main_kw에 한해서만 필터를 완화해서 다시 검색
        if require_keyword_in_title and not articles:
            articles = run_search_for_kw_list(
                kw_list,
                start_date,
                end_date,
                require_flag=False,          # 제목/본문 키워드 강제 조건 해제
                relaxed_flag=True,          # 후단 필터에서 '완화 케이스'로 인식
            )

        st.session_state.search_results[main_kw] = articles
        if main_kw not in st.session_state.show_limit:
            st.session_state.show_limit[main_kw] = 5

# --- CSS 스타일 ---
st.markdown("""
<style>
[data-testid="column"] > div { gap: 0rem !important; }
.stMultiSelect [data-baseweb="tag"] { background-color: #ff5c5c !important; color: white !important; border: none !important; font-weight: bold; }
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
.sentiment-neutral  { background: #6c757d; color: #fff; }
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

def get_industry_credit_keywords():
    return """
보험사: 수익성, 자본적정성, IFRS17, K-ICS, 리스크관리, 손해율, 재보험, 유동성, 투자자산, 스트레스테스트, 경영투명성, 내부통제, 시장지위, 자금조달, 정책, 규제, 대체투자, 손익변동, 지급여력, 계약유지율, 위험집중, 체증률, 보험금지급
5대금융지주 및 은행: 자회사 신용도, 배당, 자산건전성, 정부지원, 자본비율, 유동성비율, 대손충당금, 레버리지, 스트레스, 시장위험, 금리위험, 비이자수익, 다각화, 거버넌스, 규제준수, 운영위험, 단기부채, 구조조정, 부실채권, 조기경보, 유가증권
카드사: 시장점유율, 수수료율, 대손비용, 자산건전성, 신용리스크, 대손율, 상환능력, 포트폴리오, 수익성, 거래량, 운영리스크, 법률, 파트너십, 비용, 금융조달, 신용지원, 경쟁력, 가격책정, 승인거래액, 부정사용, 결제연체
캐피탈: 사업통합, 수익안정성, 자산건전성, 해외시장, 부실률, 자금조달, 유동성, 이익창출력, 성장성, 신용리스크, 시장리스크, 법적제약, 내부통제, 채권포트폴리오, 파생상품, 그룹지원, 사업다각화, 리스크집중도, 대출채권, 부실채권비율, 회수율
지주사: 자회사 신용도, 배당안정성, 재무부담, 그룹신용, 지배구조, 재무레버리지, 부채만기, 신용지원, 수익안정성, 자본조달, 자산건전성, 현금흐름, 자본성증권, 투자리스크, 전략지원, 지분율, 내부거래, 경영권위험
에너지: 시장경쟁, 사업다각화, 해외실적, 투자규모, 가격변동성, 재무안정성, 정책변화, 환경규제, 현금흐름, 프로젝트집행, 재무파생상품리스크, 부채구조, 자본조달, 공급망, 기술전환, 글로벌경제, 탄소배출권, 에너지수급, 정부지원
발전: 전력기반, 설비투자, 전력가격, 가동률, 계약, 연료비, 부채, 자본구조, 배당정책, 재무유연성, 정부규제, 환경법규, 현금흐름, 투자계획, 차입금, 기술리스크, 사업다각화, 시장수요, 발전효율, 신재생에너지, 정부보조금
자동차: 배터리시장, 전기차수요, 설비투자, 수익성, 시장점유율, 기술경쟁력, 매출다각화, 레버리지, 고정비, 생산능력, 신제품개발, 정부정책, 공급망, 자본지출, 연구개발, 현금흐름, 성장전망, 경쟁환경, 친환경차, 관세정책
전기전자: 반도체시장, AI수요, 무역규제, 기술우위, 제품수요, 관세, 투자계획, 생산시설, 재무안정성, 연구개발, 공급망, 진입장벽, 운영효율, 환율, 보안, 가격경쟁력, 인재확보, 재무정책, 기술특허, 보안위협
소비재: 유통변화, M&A재무부담, 온라인사업, 유통채널, 브랜드, 시장점유율, 영업이익률, 현금흐름, 재무건전성, 재고관리, 경쟁압력, 혁신, 고객충성도, 비용, 공급망, 신용지원, 매출성장, 신제품런칭, 고객확보
비철철강: 수요공급, 가격변동, 해외프로젝트, 친환경설비, 비용, 자본지출, 실행력, 환경규제, 부채, 현금흐름, 시장다변화, 상품포트폴리오, 경쟁, 공급망, 기술전환, 원자재가격, 수출비중
석유화학: 경쟁력, 포트폴리오, 투자, 차입금, 세제, 재무관리, 업황민감도, 차입금비율, 자금조달, 인수합병, 수익성, 현금흐름, 자산유동화, 리스크분산, 시장점유율, 비용, 비핵심자산, 프로젝트관리, 세제혜택
특수채: 준정부기관, 보증시장, 보증사고, 자본확충, 정부지원, 신용연계, 보증잔액, 리스크, 현금성자산, 단기부채, 미회수채권, 자산건전성, 운영안정성, 보증한도, 재무안정성, 시장지위, 관리체계, 정책, 채권발행, 지급유예, 불확실성
"""
# --- REPLACE: summarizer with clearer roles (사실 vs 영향) ---
def summarize_and_sentiment_with_openai(text, do_summary=True, target_keyword=None):
    """
    반환: (one_line_summary, keywords, sentiment, detailed_implication, short_implication, original_text)
    - 한 줄 요약: '무슨 일이 일어났는가' (사실 중심)
    - 심층 시사점: 신용평가 코멘트 형식(등급/전망/유동성/현금흐름 등 영향) 3문장 이상
    - 한 줄 시사점: 영향의 핵심 포인트만 축약
    - 감성: 긍정/부정/중립
    """
    if not OPENAI_API_KEY:
        return "OpenAI API 키가 설정되지 않았습니다.", "", "감성 추출 실패", "", "", text
    if not text or "본문 추출 오류" in text:
        return "기사 본문이 추출 실패", "", "감성 추출 실패", "", "", text

    industry_keywords = get_industry_credit_keywords()

    prompt = f"""
[참고: 산업군별 신용평가 키워드(참고용)]
{industry_keywords}

아래 [기사 본문]을 분석해 지정된 형식으로만 응답하시오.
대상 기업: "{target_keyword or 'N/A'}"

요구 형식:
1. [한 줄 요약]: 사실 중심. 누가/무엇을/언제/어떻게 한 일을 한 문장으로.
2. [심층 시사점]: 신용평가사의 코멘트 형식으로 등급/전망/재무안정성/현금흐름/유동성/사업·규제 환경 영향 분석(3문장 이상, 과도한 일반화 금지).
3. [한 줄 시사점]: 영향의 핵심 포인트만 압축(예: '차입 확대로 단기유동성 부담 상승').
4. [감성]: 긍정/부정/중립 중 하나.
5. [검색 키워드]: 대상 기업명 또는 주요 엔티티 위주로 콤마 구분.
6. [주요 키워드]: 인물/기업/기관명 중심으로 콤마 구분. 없으면 '없음'.

[기사 본문]
{text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",              # 기존 gpt-3.5-turbo → gpt-4o-mini
            messages=[
                {"role": "system", "content": "너는 신용평가사 애널리스트다. 사실 기반으로만 판단하고 과장/추측을 피한다."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=900,
            temperature=0.3                   # 0 → 0.3: 억지스러움 완화, 문장 자연스러움 개선
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 오류: {e}", "", "감성 추출 실패", "", "", text

    # --- parser ---
    def extract_group(tag):
        # 태그별 블록 추출
        pattern = rf"\[{tag}\]:\s*([\s\S]+?)(?=\n\[\w+\]:|\n\d+\. \[|$)"
        m = re.search(pattern, answer)
        return m.group(1).strip() if m else ""

    one_line = extract_group("한 줄 요약") or "요약 추출 실패"
    detailed_implication = extract_group("심층 시사점") or "시사점 추출 실패"
    short_implication = extract_group("한 줄 시사점") or "한 줄 시사점 요약 실패"
    sentiment = extract_group("감성") or "감성 추출 실패"
    keywords = extract_group("검색 키워드") or ""
    key_entities = extract_group("주요 키워드") or ""

    # 감성 표준화
    s = sentiment.strip().lower()
    if "긍" in s or "positive" in s:
        sentiment = "긍정"
    elif "부" in s or "negative" in s:
        sentiment = "부정"
    elif "중립" in s or "neutral" in s:
        sentiment = "중립"
    else:
        sentiment = "감성 추출 실패"

    return one_line, keywords, sentiment, detailed_implication, short_implication, text

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

# --- OPTIONAL: keep existing function, just ensure fallback args are passed ---
def summarize_article_from_url(article_url, title, do_summary=True, target_keyword=None, description=None):
    cache_key_base = re.sub(r"\W+", "", article_url)[-16:]
    summary_key = f"summary_{cache_key_base}"

    if summary_key in st.session_state:
        return st.session_state[summary_key]

    try:
        full_text = extract_article_text(article_url, fallback_desc=description, fallback_title=title)
        if full_text.startswith("본문 추출 오류"):
            result = (full_text, "", "감성 추출 실패", "", "", full_text)
        else:
            one_line, summary, sentiment, implication, short_implication, text = summarize_and_sentiment_with_openai(
                full_text, do_summary=do_summary, target_keyword=target_keyword
            )
            result = (one_line, summary, sentiment, implication, short_implication, text)
    except Exception as e:
        result = (f"요약 오류: {e}", "", "감성 추출 실패", "", "", "")

    st.session_state[summary_key] = result
    return result

def or_keyword_filter(article, *keyword_lists):
    text = (article.get("title", "") + " " + article.get("description", "") + " " + article.get("full_text", ""))
    for keywords in keyword_lists:
        if any(kw in text for kw in keywords if kw):
            return True
    return False

def article_contains_exact_keyword(article, keywords):
    """제목/본문(요약/본문 캐시 포함)에 키워드가 하나라도 정확히 포함되면 True"""
    if not keywords:
        return False

    # 기본 텍스트: 제목 + 네이버 description
    text_parts = [
        article.get("title", "") or "",
        article.get("description", "") or "",
    ]

    # 요약/본문이 이미 캐시에 있으면 같이 검색에 포함
    link = article.get("link", "") or ""
    if link:
        cache_key_base = re.sub(r"\W+", "", link)[-16:]
        summary_key = f"summary_{cache_key_base}"
        cached = st.session_state.get(summary_key)
        if isinstance(cached, tuple):
            # (one_line, summary, sentiment, implication, short_implication, full_text)
            one_line = cached[0] if len(cached) > 0 else ""
            summary = cached[1] if len(cached) > 1 else ""
            full_text = cached[5] if len(cached) > 5 else ""
            text_parts.extend([one_line or "", summary or "", full_text or ""])

    text = " ".join(text_parts)

    for kw in keywords:
        if kw and kw in text:
            return True
    return False

def article_passes_all_filters(article):
    # 1) 제목에 제외 키워드가 포함되면 무조건 제외
    if exclude_by_title_keywords(article.get('title', ''), EXCLUDE_TITLE_KEYWORDS):
        return False

    # 2) 날짜 범위 필터링
    try:
        pub_date = datetime.strptime(article['date'], '%Y-%m-%d').date()
        if pub_date < st.session_state.get("start_date") or pub_date > st.session_state.get("end_date"):
            return False
    except Exception:
        return False

    # 3) 키워드 집합 구성: 직접 입력 + 선택 카테고리의 회사 리스트
    all_keywords = []
    if "keyword_input" in st.session_state and st.session_state["keyword_input"]:
        all_keywords.extend([k.strip() for k in st.session_state["keyword_input"].split(",") if k.strip()])
    if "cat_multi" in st.session_state:
        for cat in st.session_state["cat_multi"]:
            all_keywords.extend(favorite_categories.get(cat, []))

    # 강력 키워드 필터 여부
    require_kw = st.session_state.get("require_exact_keyword_in_title_or_content", False)
    # 이 기사가 fallback(필터 완화) 검색으로 가져온 기사인지 여부
    relaxed = article.get("relaxed_keyword_match", False)

    # 4) 키워드 필터 통과 여부
    #    - 강력필터 ON & fallback 아님 & 키워드 존재 → 제목/본문에 반드시 포함
    #    - 그 외(체크박스 OFF, 키워드 없음, fallback 기사)는 True 로 완화
    if require_kw and not relaxed and all_keywords:
        keyword_passed = article_contains_exact_keyword(article, all_keywords)
    else:
        keyword_passed = True

    # 5) 언론사 도메인 필터
    if st.session_state.get("filter_allowed_sources_only", True):
        source = (article.get('source', '') or '').lower()
        if source.startswith("www."):
            source = source[4:]
        if source not in ALLOWED_SOURCES:
            return False

    # 6) 공통 필터(항상 AND)
    common_passed = or_keyword_filter(article, ALL_COMMON_FILTER_KEYWORDS)
    if not common_passed:
        return False

    # 7) 산업별 필터 (선택적)
    industry_passed = True
    if st.session_state.get("use_industry_filter", False):
        industry_passed = False
        keyword_for_mapping = article.get("키워드")
        matched_major = None
        for cat, companies in favorite_categories.items():
            if keyword_for_mapping in companies:
                majors = get_industry_majors_from_favorites([cat])
                if majors:
                    matched_major = majors[0]
                    break
        if matched_major:
            sub_keyword_filter = st.session_state.industry_major_sub_map.get(matched_major, [])
            if sub_keyword_filter:
                industry_passed = or_keyword_filter(article, sub_keyword_filter)

    # 8) 최종 판단
    #    - 강력 키워드 필터가 ON 인 경우:
    #        → keyword_passed=False 이면 무조건 제외
    #        → 산업필터는 추가 조건(체크 시 통과 필요)
    if require_kw:
        if not keyword_passed:
            return False
        if st.session_state.get("use_industry_filter", False) and not industry_passed:
            return False
        return True

    #    - 강력 키워드 필터가 OFF 인 경우:
    #        → 공통필터는 이미 통과했으므로,
    #        → 산업필터 또는 키워드필터 중 하나만 통과해도 허용
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
    import pandas as pd
    from io import BytesIO

    def clean_text(text):
        if not isinstance(text, str):
            text = str(text)
        text = text.replace('"', "'").replace('\n', ' ').replace('\r', '')
        return text[:200]

    # 회사 리스트 (중복 제거하며 순서 유지)
    sector_list = []
    for cat in favorite_categories:
        sector_list.extend(favorite_categories[cat])
    sector_list = list(dict.fromkeys(sector_list))

    # 각 회사에 대응하는 엑셀 표기명 리스트
    excel_sector_list = []
    for cat in excel_company_categories:
        excel_sector_list.extend(excel_company_categories[cat])
    excel_sector_list = list(dict.fromkeys(excel_sector_list))

    # 빈 DataFrame일 경우 대비
    if summary_data is None or len(summary_data) == 0:
        df_empty = pd.DataFrame(columns=["기업명", "표기명", "건수", "중요뉴스1", "중요뉴스2", "시사점"])
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_empty.to_excel(writer, index=False, sheet_name='뉴스요약')
            worksheet = writer.sheets['뉴스요약']
            worksheet.set_column(0, 5, 30)
        output.seek(0)
        return output

    df = pd.DataFrame(summary_data)

    # ‘한줄시사점’ 우선, 없으면 ‘시사점’, ‘implication’ 컬럼으로 설정
    if "한줄시사점" in df.columns:
        implication_col = "한줄시사점"
    elif "시사점" in df.columns:
        implication_col = "시사점"
    elif "implication" in df.columns:
        implication_col = "implication"
    else:
        implication_col = None

    # 키워드 관련 컬럼명 결정
    if "키워드" in df.columns:
        keyword_col = "키워드"
    elif "기업명" in df.columns:
        keyword_col = "기업명"
    elif "회사명" in df.columns:
        keyword_col = "회사명"
    else:
        keyword_col = df.columns[0] if len(df.columns) > 0 else "기업명"

    rows = []
    for idx, company in enumerate(sector_list):
        # 해당 회사 관련 모든 기사 리스트 추출
        search_articles = search_results.get(company, [])

        # 공통 필터와 산업별 필터 통과 기사만 필터링 (필요시 산업별 필터 조건 추가)
        filtered_articles = []
        for article in search_articles:
            passes_common = any(kw in (article.get("title", "") + article.get("description", "")) for kw in ALL_COMMON_FILTER_KEYWORDS)
            passes_industry = True
            # 필요 시 산업별 필터링 로직 추가 가능

            if passes_common and passes_industry:
                filtered_articles.append(article)

        # 중복 기사 제거 옵션 적용
        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)

        total_count = len(filtered_articles)

        # 해당 회사의 요약 데이터(중복 제거, 필터링된) 중 최신 2개 기사 추출
        filtered_df = df[df.get(keyword_col, "") == company].sort_values(by='날짜', ascending=False)
        hl_news = ["", ""]
        implications = ["", ""]
        for i, art in enumerate(filtered_df.itertuples()):
            if i > 1:
                break
            date_val = getattr(art, "날짜", "") or ""
            title_val = getattr(art, "기사제목", "") or getattr(art, "제목", "")
            link_val = getattr(art, "링크", "") or getattr(art, "link", "")
            display_text = f"({clean_text(date_val)}){clean_text(title_val)}"
            if title_val and link_val:
                hl_news[i] = f'=HYPERLINK("{clean_text(link_val)}", "{display_text}")'
            else:
                hl_news[i] = display_text or ""

            if implication_col:
                implications[i] = getattr(art, implication_col, "") or ""
            else:
                implications[i] = ""

        # ‘한줄 시사점’을 번호 매겨 줄바꿈으로 병합 (최대 2개)
        merged_implication = ""
        if implications[0]:
            merged_implication += f"1. {implications[0]}"
        if implications[1]:
            if merged_implication:
                merged_implication += f"\n2. {implications[1]}"
            else:
                merged_implication = f"2. {implications[1]}"

        rows.append({
            "기업명": company,
            "표기명": excel_sector_list[idx] if idx < len(excel_sector_list) else "",
            "건수": total_count,
            "중요뉴스1": hl_news[0],
            "중요뉴스2": hl_news[1],
            "시사점": merged_implication
        })

    result_df = pd.DataFrame(rows, columns=["기업명", "표기명", "건수", "중요뉴스1", "중요뉴스2", "시사점"])

    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        result_df.to_excel(writer, index=False, sheet_name='뉴스요약')
        worksheet = writer.sheets['뉴스요약']
        for i, col in enumerate(result_df.columns):
            worksheet.set_column(i, i, 30)
    output.seek(0)
    return output

def generate_important_article_list(search_results, common_keywords, industry_keywords, favorites):
    """
    OpenAI를 이용해 '신용평가 관점에서 중요한 기사'를 자동 선정.
    - 각 기사에 대해 신용영향도(1~5점)를 평가하게 하고
    - 반드시 5점 기사만 자동 선정 대상으로 사용.
    - 결과는 기사 번호 기반으로 파싱하여 원본 기사(dict)를 반환.
    """
    import os
    from openai import OpenAI
    import re

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
    result = []

    # 섹터별 키워드 파싱 (get_industry_credit_keywords() 기반)
    def parse_industry_keywords():
        raw_text = get_industry_credit_keywords()
        industry_dict = {}
        for line in raw_text.strip().split("\n"):
            if ":" in line:
                sector, keywords = line.split(":", 1)
                industry_dict[sector.strip()] = [
                    kw.strip() for kw in keywords.split(",") if kw.strip()
                ]
        return industry_dict

    industry_keywords_dict = parse_industry_keywords()

    # ---- 각 카테고리(섹터) / 회사별로 중요 기사 선정 ----
    for category, companies in favorites.items():
        sector_keywords = industry_keywords_dict.get(category, [])

        for comp in companies:
            articles = search_results.get(comp, [])
            if not articles:
                continue

            # 섹터 키워드가 제목/설명에 어느 정도 포함된 기사만 1차 필터
            target_articles = []
            for a in articles:
                text = (a.get("title", "") + " " + a.get("description", "")).lower()
                if sector_keywords and any(kw.lower() in text for kw in sector_keywords):
                    target_articles.append(a)
                elif not sector_keywords:
                    # 섹터 키워드가 정의되지 않은 경우에는 전부 후보로 사용
                    target_articles.append(a)

            if not target_articles:
                continue

            # 기사 목록을 "번호. 제목 - 링크" 형태로 구성
            prompt_list = "\n".join(
                [f"{i+1}. {a['title']} - {a['link']}" for i, a in enumerate(target_articles)]
            )

            # --- 프롬프트: 5점 기사만 자동 선정 ---
            guideline = f"""
당신은 신용평가사 애널리스트입니다.

[신용영향도 판단 기준]
5점: 신용등급/전망 변화 가능성, 대규모 자본확충·차입, 유동성 위기, 부도·법정관리·회생 신청, 중대한 규제·제재·소송 등
4점: 대규모 투자·M&A·지분매각, 실적 급변(큰 폭의 흑자/적자 변화), 레버리지 급증, 계열사의 신용위험이 본사에 중대한 영향을 줄 가능성
3점: 일반적인 실적 개선/악화, 중간 규모의 자금조달, 사업 포트폴리오 조정(비핵심자산 매각 등)
2점: 신제품 출시, 마케팅/프로모션, 제휴·MOU, 일반적인 사업 계획 등 신용도에 미치는 영향이 제한적인 뉴스
1점: 사회공헌/행사/ESG 홍보, 단순 이미지 제고, 연예·문화·스포츠 등 신용과 거의 무관한 내용

[기사 목록]
{prompt_list}

분석의 초점은 반드시 "{comp}" 기업(또는 키워드)이며,
"{category}" 산업의 신용평가 관점에서 각 뉴스가 신용도에 미치는 영향도를 위 기준으로 평가하십시오.

[지시사항]
1. 각 기사 번호별로 신용영향도 점수(1~5점)를 한 번씩만 매기십시오.
2. 반드시 **5점인 기사만** '중요 기사 후보'로 간주하십시오.
3. 5점인 기사 중에서 가장 중요한 기사 최대 2건의 "번호"만 선택하십시오.
   - 5점 기사 2건 이상이면 그 중에서 상위 2건만 선택하십시오.
   - 5점 기사 1건이면 그 1건만 선택하십시오.
   - 5점 기사 0건이면 어떤 기사도 선택하지 마십시오.
4. 선택된 번호가 없을 수도 있습니다. 이 경우에도 아래 [선정] 형식은 유지하되 '없음'이라고 적으십시오.

출력 형식은 반드시 아래 형식만 사용하십시오. 설명 문장은 넣지 마십시오.

[평가]
1번: (점수)
2번: (점수)
...

[선정]
[중요1]: (기사번호 또는 없음)
[중요2]: (기사번호 또는 없음)
"""

            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": guideline}],
                    max_tokens=600,
                    temperature=0,
                )
                answer = response.choices[0].message.content.strip()

                # --- [평가]에서 각 기사 점수 파싱 ---
                score_map = {}
                for line in answer.splitlines():
                    m = re.match(r"(\d+)번\s*:\s*([0-9]+)", line.strip())
                    if m:
                        no = int(m.group(1))
                        score = int(m.group(2))
                        score_map[no] = score

                # --- 선택된 기사 번호 파싱 ---
                sel1_match = re.search(r"\[중요 ?1\]\s*:\s*(\d+)", answer)
                sel2_match = re.search(r"\[중요 ?2\]\s*:\s*(\d+)", answer)

                raw_selected = []
                if sel1_match:
                    raw_selected.append(int(sel1_match.group(1)))
                if sel2_match:
                    raw_selected.append(int(sel2_match.group(1)))

                selected_indexes = []
                for no in raw_selected:
                    idx0 = no - 1
                    # ✅ 실제 점수가 5점인 것만 유지
                    if score_map.get(no) == 5 and 0 <= idx0 < len(target_articles):
                        if idx0 not in selected_indexes:
                            selected_indexes.append(idx0)

                # ✅ 5점이 없으면 skip
                if not selected_indexes:
                    continue
                    
            except Exception:
                # 에러 시 이 회사에 대해서는 자동선정 건너뜀
                continue

    return result

# --- REPLACE: robust article text extractor ---
def extract_article_text(url, fallback_desc=None, fallback_title=None):
    """
    우선 newspaper로 시도 → 실패 시 BeautifulSoup <p> 기반 수동 추출 → 그래도 실패하면
    title/description을 최소 텍스트로 반환하여 요약이 동작하도록 보장.
    """
    # 1) newspaper 1차 시도
    try:
        import newspaper
        art = newspaper.Article(url, language="ko")
        art.download()
        art.parse()
        txt = (art.text or "").strip()
        if len(txt) >= 300:
            return txt
    except Exception:
        pass

    # 2) BeautifulSoup fallback (<p> 기반)
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # 기사 컨테이너 우선 탐색
        candidates = [
            "article", ".article", ".news_body", "#articleBodyContents",
            ".content", ".article-body", ".art_txt", ".article_view"
        ]
        blocks = []
        for sel in candidates:
            blocks.extend(soup.select(sel))

        paragraphs = []
        if blocks:
            for b in blocks:
                paragraphs.extend(b.select("p"))
        else:
            paragraphs = soup.select("p")

        text = " ".join(
            p.get_text(strip=True)
            for p in paragraphs
            if len(p.get_text(strip=True)) >= 30
        )
        text = " ".join(text.split())
        if len(text) >= 200:
            return text
    except Exception:
        pass

    # 3) 최소 보장 (설명/제목 기반)
    if fallback_desc or fallback_title:
        return f"{(fallback_title or '').strip()} {(fallback_desc or '').strip()}".strip()

    return "본문 추출 오류"
    
def extract_keyword_from_link(search_results, article_link):
    """
    뉴스검색결과 dict와 기사 링크로 해당 기사의 키워드(회사명/카테고리)를 추출
    """
    for kw, arts in search_results.items():
        for art in arts:
            if art.get("link") == article_link:
                return kw
    return ""

def build_important_excel_format(important_articles, favorite_categories, excel_categories, search_results):
    import pandas as pd

    df = pd.DataFrame(important_articles)

    # 회사 리스트 (중복 제거하며 순서 유지)
    sector_list = []
    for cat in favorite_categories:
        sector_list.extend(favorite_categories[cat])
    sector_list = list(dict.fromkeys(sector_list))

    excel_sector_list = []
    for cat in excel_categories:
        excel_sector_list.extend(excel_categories[cat])
    excel_sector_list = list(dict.fromkeys(excel_sector_list))

    rows = []

    for idx, company in enumerate(sector_list):
        # 기사 필터링 및 중복 제거
        all_articles = search_results.get(company, [])

        filtered_articles = []
        for art in all_articles:
            if article_passes_filters(art):  # 또는 article_passes_filters(art) 함수에 맞게 변경
                filtered_articles.append(art)

        if 'remove_duplicate_articles' in st.session_state and st.session_state['remove_duplicate_articles']:
            filtered_articles = remove_duplicates(filtered_articles)

        total_count = len(filtered_articles)

        # 해당 회사의 선택된 중요기사 요약 데이터(이미 중복 제거, 필터링된)를 가져옴
        filtered_df = df[df['기업명'] == company].sort_values(by='날짜', ascending=False)

        hl_news = []
        for i, art in enumerate(filtered_df.itertuples()):
            if i > 1:
                break
            title = getattr(art, '제목', '') or ''
            link = getattr(art, '링크', '') or ''
            if title and link:
                hl_news.append(f'=HYPERLINK("{link}", "{title}")')
            else:
                hl_news.append(title or '')
        # 2개까지 채우고 부족하면 빈문자열 채움
        while len(hl_news) < 2:
            hl_news.append('')

        # 시사점 병합 (최대 2개)
        implication_col = '시사점' if '시사점' in df.columns else ('implication' if 'implication' in df.columns else None)
        implications = []
        for i, art in enumerate(filtered_df.itertuples()):
            if i > 1:
                break
            val = getattr(art, implication_col, '') if implication_col else ''
            implications.append(val)
        merged_implication = ''
        if implications:
            merged_implication = '\n'.join(f"{idx+1}. {txt}" for idx, txt in enumerate(implications) if txt)

        rows.append({
            '기업명': company,
            '표기명': excel_sector_list[idx] if idx < len(excel_sector_list) else '',
            '건수': total_count,
            '중요뉴스1': hl_news[0],
            '중요뉴스2': hl_news[1],
            '시사점': merged_implication
        })

    result_df = pd.DataFrame(rows, columns=['기업명', '표기명', '건수', '중요뉴스1', '중요뉴스2', '시사점'])

    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        result_df.to_excel(writer, index=False, sheet_name='뉴스요약')
        worksheet = writer.sheets['뉴스요약']
        for i, col in enumerate(result_df.columns):
            worksheet.set_column(i, i, 30)
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

    # ---------------------------- 뉴스 목록 열 ---------------------------- #
    with col_list:
        st.markdown("### 🔍 뉴스 검색 결과")
        for category_name, company_list in favorite_categories.items():
            companies_with_results = [c for c in company_list if c in results]
            if not companies_with_results:
                continue

            with st.expander(f"📂 {category_name}", expanded=True):
                for company in companies_with_results:
                    articles = results[company]

                    with st.expander(f"[{company}] ({len(articles)}건)", expanded=False):
                        # 이 회사에 속한 모든 기사 key 수집
                        all_article_keys = []
                        for idx, article in enumerate(articles):
                            uid = re.sub(r"\W+", "", article["link"])[-16:]
                            key = f"{company}_{idx}_{uid}"
                            all_article_keys.append(key)

                        # ✅ 마스터 체크박스 key 를 완전히 유일하게 생성 (카테고리+회사 기반)
                        slug = re.sub(r"\W+", "", f"{category_name}_{company}")
                        master_key = f"left_master_{slug}_select_all"

                        prev_value = all(
                            st.session_state.article_checked.get(k, False)
                            for k in all_article_keys
                        )

                        select_all = st.checkbox(
                            f"전체 기사 선택/해제 ({company})",
                            value=prev_value,
                            key=master_key,
                        )

                        # 마스터 체크박스 값이 바뀐 경우 → 개별 체크박스 & 상태 동기화
                        if select_all != prev_value:
                            for k in all_article_keys:
                                st.session_state.article_checked[k] = select_all
                                st.session_state.article_checked_left[k] = select_all
                                # 실제 개별 기사 체크박스 위젯 상태도 같이 변경
                                st.session_state[f"news_{k}"] = select_all
                            st.rerun()

                        # 개별 기사 표시
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
                                    _, _, sentiment, _, _ = st.session_state[cache_key]

                                badge_html = (
                                    f"<span class='sentiment-badge "
                                    f"{SENTIMENT_CLASS.get(sentiment, 'sentiment-neutral')}'>{sentiment}</span>"
                                    if sentiment else ""
                                )
                                search_word_info = (
                                    f" | 검색어: {article.get('검색어', '')}"
                                    if article.get("검색어") else ""
                                )

                                st.markdown(
                                    f"<span class='news-title'><a href='{article['link']}' "
                                    f"target='_blank'>{article['title']}</a></span> "
                                    f"{badge_html} {article['date']} | {article['source']}{search_word_info}",
                                    unsafe_allow_html=True,
                                )

                            # 세션 상태 갱신
                            st.session_state.article_checked_left[key] = checked
                            st.session_state.article_checked[key] = checked


    # ---------------------------- 선택 기사 요약/감성분석 열 ---------------------------- #
    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):
            industry_keywords_all = []
            if st.session_state.get("use_industry_filter", False):
                for sublist in st.session_state.industry_major_sub_map.values():
                    industry_keywords_all.extend(sublist)

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

            def process_article(item):
                keyword, idx, art = item
                cache_key = f"summary_{keyword}_{idx}_" + re.sub(r"\W+", "", art["link"])[-16:]
                if cache_key in st.session_state:
                    one_line, summary, sentiment, implication, short_implication, full_text = st.session_state[cache_key]
                else:
                    one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
                        art["link"], art["title"], do_summary=enable_summary, target_keyword=keyword
                    )
                    st.session_state[cache_key] = (one_line, summary, sentiment, implication, short_implication, full_text)
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
                    "시사점": implication,
                    "한줄시사점": short_implication,  
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
                                    f"<span class='sentiment-badge {SENTIMENT_CLASS.get(art['감성'], 'sentiment-neutral')}'>{art['감성']}</span>",
                                    unsafe_allow_html=True
                                )
                                st.markdown(f"- **검색 키워드:** `{art['키워드']}`")
                                st.markdown(f"- **필터로 인식된 키워드:** `{art['필터히트'] or '없음'}`")
                                st.markdown(f"- **날짜/출처:** {art['날짜']} | {art['출처']}")
                                if enable_summary:
                                    st.markdown(f"- **한 줄 요약:** {art['요약']}")
                                    st.markdown(f"- **한 줄 시사점:** {art.get('한줄시사점', '없음')}")
                                    st.markdown(f"- **시사점:** {art['시사점'] or '없음'}")
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
    import re
    from collections import defaultdict
    import streamlit as st

    with st.container(border=True):
        st.markdown("### ⭐ 중요 기사 리뷰 및 편집")

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
                # key 명 통일 및 시사점 필드 포함 (시사점은 빈 문자열로 초기화, 필요 시 OpenAI 결과 반영 가능)
                for i, art in enumerate(important_articles):
                    important_articles[i] = {
                        "키워드": art.get("키워드") or art.get("회사명") or art.get("keyword") or "",
                        "기사제목": art.get("기사제목") or art.get("제목") or art.get("title") or "",
                        "감성": art.get("감성", ""),
                        "링크": art.get("링크") or art.get("link", ""),
                        "날짜": art.get("날짜") or art.get("date", ""),
                        "출처": art.get("출처") or art.get("source", ""),
                        "시사점": art.get("시사점", "")  # 시사점 필드 추가 (자동선정 시 채워질 수 있음)
                    }
                st.session_state["important_articles_preview"] = important_articles
                st.session_state["important_selected_index"] = []

        articles = st.session_state.get("important_articles_preview", [])
        selected_indexes = st.session_state.get("important_selected_index", [])

        # 대분류(major) - 소분류(minor) 그룹화
        major_map = defaultdict(lambda: defaultdict(list))  # major_map[대분류][소분류] = [기사 리스트]
        for art in articles:
            keyword = art.get("키워드") or art.get("회사명") or ""
            found_major = None
            for major, minors in favorite_categories.items():
                if keyword in minors:
                    found_major = major
                    break
            if found_major:
                major_map[found_major][keyword].append(art)

        st.markdown("🎯 **중요 기사 목록 (교체 또는 삭제할 항목을 체크하세요)**")

        from concurrent.futures import ThreadPoolExecutor
        one_line_map = {}
        to_summarize = []

        for major, minor_map in major_map.items():
            for minor, arts in minor_map.items():
                for idx, article in enumerate(arts):
                    link = article.get("링크", "")
                    cleaned_id = re.sub(r"\W+", "", link)[-16:] if link else ""
                    cache_hit = False
                    for k, v in st.session_state.items():
                        if k.startswith("summary_") and cleaned_id in k and isinstance(v, tuple):
                            one_line_map[(major, minor, idx)] = v
                            cache_hit = True
                            break
                    if not cache_hit and link:
                        to_summarize.append((major, minor, idx, link, article.get("기사제목", "")))

        if to_summarize:
            with st.spinner("중요 기사 요약 생성 중..."):
                def get_one_line(args):
                    major, minor, idx, link, title = args
                    one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(link, title, do_summary=True)
                    return (major, minor, idx), (one_line, summary, sentiment, implication, short_implication, full_text)

                with ThreadPoolExecutor(max_workers=10) as executor:
                    for key, data_tuple in executor.map(get_one_line, to_summarize):
                        one_line_map[key] = data_tuple

        new_selection = []
        if to_summarize:
            with st.spinner("중요 기사 요약 생성 중."):
                def get_one_line(args):
                    major, minor, idx, link, title = args
                    one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
                        link, title, do_summary=True
                    )
                    return (major, minor, idx), (one_line, summary, sentiment, implication, short_implication, full_text)

                with ThreadPoolExecutor(max_workers=10) as executor:
                    for key, data_tuple in executor.map(get_one_line, to_summarize):
                        one_line_map[key] = data_tuple

        new_selection = []

        for major, minor_map in major_map.items():
            with st.expander(f"📊 {major}", expanded=True):
                for minor, arts in minor_map.items():
                    with st.expander(f"{minor} ({len(arts)}건)", expanded=False):
                        for idx, article in enumerate(arts):
                            check_key = f"important_chk_{major}_{minor}_{idx}"

                            # 한 줄에 체크박스 + 감성 + 기사제목 하이퍼링크 배치
                            cols = st.columns([0.06, 0.94])

                            # ✅ 왼쪽: 체크박스
                            with cols[0]:
                                checked = st.checkbox(
                                    "",
                                    key=check_key,
                                    value=((major, minor, idx) in selected_indexes),
                                )

                            # ✅ 오른쪽: 기사 정보 및 시사점
                            with cols[1]:
                                st.markdown(
                                    f"{article.get('감성','')} | "
                                    f"<a href='{article.get('링크','')}' target='_blank'>"
                                    f"{article.get('기사제목','제목없음')}</a>",
                                    unsafe_allow_html=True,
                                )

                                # 시사점 및 한줄 시사점 출력
                                summary_data = one_line_map.get((major, minor, idx))
                                implication_text = ""
                                short_implication_text = ""

                                if summary_data and len(summary_data) == 6:
                                    implication_text = summary_data[3] or ""       # 시사점
                                    short_implication_text = summary_data[4] or ""  # 한줄 시사점
                                else:
                                    implication_text = article.get("시사점", "") or ""
                                    short_implication_text = article.get("한줄시사점", "") or ""

                                if implication_text:
                                    st.markdown(implication_text)
                                if short_implication_text:
                                    st.markdown(
                                        f"<span style='color:gray;font-style:italic;'>{short_implication_text}</span>",
                                        unsafe_allow_html=True,
                                    )

                                st.markdown(
                                    f"<span style='font-size:12px;color:#99a'>"
                                    f"{article.get('날짜', '')} | {article.get('출처', '')}</span>",
                                    unsafe_allow_html=True,
                                )

                                st.markdown(
                                    "<div style='margin:0px;padding:0px;height:4px'></div>",
                                    unsafe_allow_html=True,
                                )

                            # ✅ 선택 상태 반영
                            if checked:
                                new_selection.append((major, minor, idx))

        st.session_state["important_selected_index"] = new_selection
        
        # 추가 / 삭제 / 교체 버튼 및 해당 기능 (기존 코드 유지)
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
                            _, _, sentiment, _, _ = summarize_article_from_url(
                                selected_article["link"], selected_article["title"]
                            )
                        new_article = {
                            "키워드": keyword,
                            "기사제목": selected_article["title"],
                            "감성": sentiment or "",
                            "링크": selected_article["link"],
                            "날짜": selected_article["date"],
                            "출처": selected_article["source"],
                            "시사점": ""  # 시사점 필드 초기값 빈 문자열
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
                    st.experimental_rerun()

        with col_del:
            if st.button("🗑 선택 기사 삭제"):
                important = st.session_state.get("important_articles_preview", [])
                remove_links = []
                for major, minor, idx in st.session_state["important_selected_index"]:
                    try:
                        link = major_map[major][minor][idx]["링크"]
                        remove_links.append(link)
                    except Exception:
                        continue
                important = [a for a in important if a.get("링크") not in remove_links]
                st.session_state["important_articles_preview"] = important
                st.session_state["important_selected_index"] = []
                st.experimental_rerun()

        with col_rep:
            if st.button("🔁 선택 기사 교체"):
                left_selected_keys = [k for k, v in st.session_state.article_checked_left.items() if v]
                right_selected_indexes = st.session_state["important_selected_index"]
                if len(left_selected_keys) != 1 or len(right_selected_indexes) != 1:
                    st.warning("왼쪽 1개, 오른쪽 1개만 선택해주세요.")
                    return
                from_key = left_selected_keys[0]
                (target_major, target_minor, target_idx) = right_selected_indexes[0]
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
                    _, _, sentiment, _, _ = summarize_article_from_url(
                        selected_article["link"], selected_article["title"]
                    )
                important = st.session_state.get("important_articles_preview", [])
                remove_link = major_map[target_major][target_minor][target_idx]["링크"]
                important = [a for a in important if a.get("링크") != remove_link]
                new_article = {
                    "키워드": keyword,
                    "기사제목": selected_article["title"],
                    "감성": sentiment or "",
                    "링크": selected_article["link"],
                    "날짜": selected_article["date"],
                    "출처": selected_article["source"],
                    "시사점": ""  # 시사점 필드 초기값 빈 문자열
                }
                important.append(new_article)
                st.session_state["important_articles_preview"] = important
                st.session_state.article_checked_left[from_key] = False
                st.session_state.article_checked[from_key] = False
                st.session_state["important_selected_index"] = []
                st.success("중요 기사 교체 완료")
                st.experimental_rerun()

        st.markdown("---")
        st.markdown("📥 **리뷰한 중요 기사들을 엑셀로 다운로드하세요.**")
        articles_source = st.session_state.get("important_articles_preview", [])
        industry_keywords_all = []
        if st.session_state.get("use_industry_filter", False):
            for sublist in st.session_state.industry_major_sub_map.values():
                industry_keywords_all.extend(sublist)

        def enrich_article_for_excel(raw_article):
            link = raw_article.get("링크", "")
            keyword = raw_article.get("키워드", "")
            cleaned_id = re.sub(r"\W+", "", link)[-16:]

            one_line, summary, sentiment, implication, short_implication, full_text = None, None, None, None, None, None

            for k, v in st.session_state.items():
                if k.startswith("summary_") and cleaned_id in k and isinstance(v, tuple):
                    one_line, summary, sentiment, implication, short_implication, full_text = v
                    break

            if not sentiment:
                one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
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
                "시사점": implication,
                "한줄시사점": short_implication,   # 한줄 시사점 필드 추가
                "링크": link,
                "날짜": raw_article.get("날짜", ""),
                "출처": raw_article.get("출처", ""),
                "full_text": full_text or "",
            }
        summary_data = [enrich_article_for_excel(a) for a in articles_source]

        # 여기에서 엑셀 생성 시 한줄시사점 반영하여 통합
        def get_excel_with_joined_implications(summary_data, favorite_categories, excel_company_categories, search_results):
            import pandas as pd
            from io import BytesIO

            if not summary_data or len(summary_data) == 0:
                df_empty = pd.DataFrame(columns=["기업명", "표기명", "건수", "중요뉴스1", "중요뉴스2", "시사점"])
                output = BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df_empty.to_excel(writer, index=False, sheet_name='뉴스요약')
                    worksheet = writer.sheets['뉴스요약']
                    worksheet.set_column(0, 5, 30)
                output.seek(0)
                return output

            df = pd.DataFrame(summary_data)

            # 회사 리스트 (중복 제거 및 순서 유지)
            sector_list = []
            for cat in favorite_categories:
                sector_list.extend(favorite_categories[cat])
            sector_list = list(dict.fromkeys(sector_list))

            excel_sector_list = []
            for cat in excel_company_categories:
                excel_sector_list.extend(excel_company_categories[cat])
            excel_sector_list = list(dict.fromkeys(excel_sector_list))

            rows = []
            for idx, company in enumerate(sector_list):
                search_articles = search_results.get(company, [])

                filtered_articles = []
                for article in search_articles:
                    passes_common = any(kw in (article.get("title", "") + article.get("description", "")) for kw in ALL_COMMON_FILTER_KEYWORDS)
                    passes_industry = True
                    # 필요 시 산업별 필터링 로직 추가 가능

                    if passes_common and passes_industry:
                        filtered_articles.append(article)

                if st.session_state.get("remove_duplicate_articles", False):
                    filtered_articles = remove_duplicates(filtered_articles)

                total_count = len(filtered_articles)

                filtered_df = df[df.get("키워드", "") == company].sort_values(by='날짜', ascending=False)

                hl_news = ["", ""]
                implications = ["", ""]
                short_imps = ["", ""]

                for i, art in enumerate(filtered_df.itertuples()):
                    if i > 1:
                        break
                    date_val = getattr(art, "날짜", "") or ""
                    title_val = getattr(art, "기사제목", "") or getattr(art, "제목", "")
                    link_val = getattr(art, "링크", "") or getattr(art, "link", "")
                    short_imp_val = getattr(art, "한줄시사점", "") or ""

                    display_text = f"({clean_excel_formula_text(date_val)}){clean_excel_formula_text(title_val)}"
                    if title_val and link_val:
                        hl_news[i] = f'=HYPERLINK("{clean_excel_formula_text(link_val)}", "{display_text}")'
                    else:
                        hl_news[i] = display_text or ""

                    implications[i] = getattr(art, "시사점", "") or ""
                    short_imps[i] = short_imp_val

                # 시사점 및 한줄시사점 번호 붙여서 병합
                merged_implications = ""
                for n in range(2):
                    if implications[n]:
                        merged_implications += f"{n+1}. {implications[n]}\n"
                for n in range(2):
                    if short_imps[n]:
                        merged_implications += f"{n+1}. {short_imps[n]}\n"

                rows.append({
                    "기업명": company,
                    "표기명": excel_sector_list[idx] if idx < len(excel_sector_list) else "",
                    "건수": total_count,
                    "중요뉴스1": hl_news[0],
                    "중요뉴스2": hl_news[1],
                    "시사점": merged_implications.strip(),
                })

            result_df = pd.DataFrame(rows, columns=["기업명", "표기명", "건수", "중요뉴스1", "중요뉴스2", "시사점"])

            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                result_df.to_excel(writer, index=False, sheet_name='뉴스요약')
                worksheet = writer.sheets['뉴스요약']
                for i, col in enumerate(result_df.columns):
                    worksheet.set_column(i, i, 30)
            output.seek(0)
            return output

        excel_data = get_excel_with_joined_implications(summary_data, favorite_categories, excel_company_categories, st.session_state.search_results)

        st.download_button(
            label="📥 중요 기사 최종 엑셀 다운로드 (맞춤 양식)",
            data=excel_data.getvalue(),
            file_name=f"중요뉴스_최종선정_양식_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if st.session_state.get("search_results"):
    filtered_results = {}
    for keyword, articles in st.session_state["search_results"].items():
        filtered_articles = [a for a in articles if article_passes_all_filters(a)]

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

    selected_companies = []
    for cat in st.session_state.get("cat_multi", []):
        selected_companies.extend(favorite_categories.get(cat, []))
    selected_companies = list(set(selected_companies))

    # kiscd_map과 cmpCD_map 모두에서 회사명에 매칭되는 키 값 가져오기
    kiscd_filtered = {c: kiscd_map[c] for c in selected_companies if c in kiscd_map}
    cmpcd_filtered = {c: config.get("cmpCD_map", {}).get(c, "") for c in selected_companies}

    # 두 맵을 합치는 함수 (kiscd_filtered 기본에 cmpcd_filtered도 합칠 수 있도록)
    # fetch_and_display_reports가 kiscd만 받으므로 확장 필요
    # 여기서는 kiscd_filtered 넘기고, fetch_and_display_reports 내부에서 cmpCD_map 참조 권장

    fetch_and_display_reports(kiscd_filtered)

else:
    st.info("뉴스 검색 결과가 없습니다. 먼저 검색을 실행해 주세요.")

