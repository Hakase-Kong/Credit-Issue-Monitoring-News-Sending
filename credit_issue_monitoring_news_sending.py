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
from bs4 import BeautifulSoup
import hashlib

# =========================================================
# 0. config 로드
# =========================================================
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

EXCLUDE_TITLE_KEYWORDS = config["EXCLUDE_TITLE_KEYWORDS"]
ALLOWED_SOURCES = set(config["ALLOWED_SOURCES"])
favorite_categories = config["favorite_categories"]
excel_company_categories = config["excel_company_categories"]
common_filter_categories = config["common_filter_categories"]
industry_filter_categories = config["industry_filter_categories"]
SYNONYM_MAP = config["synonym_map"]
kiscd_map = config.get("kiscd_map", {})
kr_compcd_map = config.get("kr_COMP_CD_map", {})

# 공통 필터 키워드 전체 리스트
ALL_COMMON_FILTER_KEYWORDS = []
for keywords in common_filter_categories.values():
    ALL_COMMON_FILTER_KEYWORDS.extend(keywords)


# =========================================================
# 1. 유틸
# =========================================================
def get_sector_of_company(company: str):
    for sector, comps in favorite_categories.items():
        if company in comps:
            return sector
    return None

def detect_lang(text):
    return "ko" if re.search(r"[가-힣]", text) else "en"

def make_uid(url: str, length: int = 16) -> str:
    if not url:
        return "no_url"
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:length]

def infer_source_from_url(url):
    domain = urlparse(url).netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

def exclude_by_title_keywords(title, exclude_keywords):
    for word in exclude_keywords:
        if word in title:
            return True
    return False

def is_similar(title1, title2, threshold=0.75):
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

def safe_title(val):
    if pd.isnull(val) or str(val).strip() == "" or str(val).lower() == "nan" or str(val) == "0":
        return "제목없음"
    return str(val)

def clean_excel_formula_text(text):
    if not isinstance(text, str):
        text = str(text)
    text = text.replace('"', "'").replace('\n', ' ').replace('\r', '')
    return text[:250]


# =========================================================
# 2. 산업 키워드 / 파서
# =========================================================
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

def parse_industry_credit_keywords():
    raw_text = get_industry_credit_keywords()
    industry_dict = {}
    for line in raw_text.strip().split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        sector, kws = line.split(":", 1)
        industry_dict[sector.strip()] = [
            kw.strip() for kw in kws.split(",") if kw.strip()
        ]
    return industry_dict

def expand_keywords_with_synonyms(original_keywords):
    expanded_map = {}
    for kw in original_keywords:
        synonyms = SYNONYM_MAP.get(kw, [])
        expanded_map[kw] = [kw] + synonyms
    return expanded_map


# =========================================================
# 3. OpenAI / 요약 / 감성
# =========================================================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def summarize_and_sentiment_with_openai(text, do_summary=True, target_keyword=None):
    if not OPENAI_API_KEY or client is None:
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
- 1. [한 줄 요약]:
  * 사실 중심(누가/무엇을/언제/어떻게).
  * 2~3문장 이내로 핵심 사실 요약.
  * 추측/평가 금지.
- 2. [심층 시사점]: 신용평가사의 코멘트 형식으로 등급/전망/재무안정성/현금흐름/유동성/사업·규제 환경 영향 분석(3문장 이상).
- 3. [한 줄 시사점]: 영향의 핵심 포인트만 압축.
- 4. [감성]: 긍정/부정/중립 중 하나.
- 5. [검색 키워드]: 대상 기업명 또는 주요 엔티티 위주로 콤마 구분.
- 6. [주요 키워드]: 인물/기업/기관명 중심으로 콤마 구분. 없으면 '없음'.

[기사 본문]
{text}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "너는 신용평가사 애널리스트다. 사실 기반으로만 판단하고 과장/추측을 피한다."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=900,
            temperature=0.3
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 오류: {e}", "", "감성 추출 실패", "", "", text

    def extract_group(tag):
        pattern = rf"\[{tag}\]:\s*([\s\S]+?)(?=\n\[\w+\]:|\n\d+\. \[|$)"
        m = re.search(pattern, answer)
        return m.group(1).strip() if m else ""

    def clean_llm_text(t: str) -> str:
        if not t:
            return t
        cleaned_lines = []
        for ln in t.splitlines():
            ln = re.sub(r"^\s*\d+\.\s*", "", ln).strip()
            if not ln:
                continue
            if re.fullmatch(r"\d+", ln):
                continue
            cleaned_lines.append(ln)
        return "\n".join(cleaned_lines).strip()

    one_line = clean_llm_text(extract_group("한 줄 요약") or "요약 추출 실패")
    detailed_implication = clean_llm_text(extract_group("심층 시사점") or "시사점 추출 실패")
    short_implication = clean_llm_text(extract_group("한 줄 시사점") or "한 줄 시사점 요약 실패")
    sentiment = extract_group("감성") or "감성 추출 실패"
    keywords = extract_group("검색 키워드") or ""
    key_entities = extract_group("주요 키워드") or ""

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


# =========================================================
# 4. 기사 본문 추출
# =========================================================
def extract_article_text(url, fallback_desc=None, fallback_title=None):
    try:
        art = newspaper.Article(url, language="ko")
        art.download()
        art.parse()
        txt = (art.text or "").strip()
        if len(txt) >= 300:
            return txt
    except Exception:
        pass

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

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

    if fallback_desc or fallback_title:
        return f"{(fallback_title or '').strip()} {(fallback_desc or '').strip()}".strip()

    return "본문 추출 오류"


def get_summary_key_from_url(article_url: str, target_keyword: str = None) -> str:
    uid = make_uid(article_url)
    if target_keyword and str(target_keyword).strip():
        return f"summary_{target_keyword}_{uid}"
    return f"summary_{uid}"

def summarize_article_from_url(article_url, title, do_summary=True, target_keyword=None, description=None):
    uid = make_uid(article_url)
    if target_keyword and str(target_keyword).strip():
        summary_key = f"summary_{target_keyword}_{uid}"
    else:
        summary_key = f"summary_{uid}"

    if summary_key in st.session_state:
        return st.session_state[summary_key]

    try:
        full_text = extract_article_text(
            article_url,
            fallback_desc=description,
            fallback_title=title
        )

        if full_text.startswith("본문 추출 오류"):
            result = (full_text, "", "감성 추출 실패", "", "", full_text)
        else:
            one_line, summary, sentiment, implication, short_implication, text = summarize_and_sentiment_with_openai(
                full_text,
                do_summary=do_summary,
                target_keyword=target_keyword
            )
            result = (one_line, summary, sentiment, implication, short_implication, text)
    except Exception as e:
        result = (f"요약 오류: {e}", "", "감성 추출 실패", "", "", "")

    st.session_state[summary_key] = result
    return result


# =========================================================
# 5. Naver 뉴스 수집
# =========================================================
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

class Telegram:
    def __init__(self):
        self.bot = telepot.Bot(TELEGRAM_TOKEN)
        self.chat_id = TELEGRAM_CHAT_ID
    def send_message(self, message):
        self.bot.sendMessage(self.chat_id, message, parse_mode="Markdown", disable_web_page_preview=True)

def filter_by_issues(title, desc, selected_keywords, require_keyword_in_title=False):
    if require_keyword_in_title and selected_keywords:
        text = (title + " " + desc).lower()
        if not any(kw.lower() in text for kw in selected_keywords):
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
                "description": desc,
                "link": real_link,
                "date": pub_date.strftime("%Y-%m-%d"),
                "source": source_domain
            })

        if len(items) < 100:
            break
    return articles[:limit]


# =========================================================
# 6. LLM 점수 / 필터
# =========================================================
def llm_score_articles_batch(articles, target_keyword=None, mode="company"):
    if not OPENAI_API_KEY or client is None:
        return {i: 3 for i in range(len(articles))}

    prompt_list = "\n".join(
        [f"{i+1}. {a.get('title','')} || {a.get('description','')}" for i, a in enumerate(articles)]
    )

    if mode == "industry":
        guideline = f"""
너는 신용평가사 산업 애널리스트다. 아래 기사 제목/요약을 보고
산업 대분류 "{target_keyword or 'N/A'}" 관점에서 산업 전반 영향도를 1~5점으로 평가하라.

5점: 산업 구조/규제/정책/금융여건/수요·공급/가격결정 구조/경쟁구도 변화 등 다수 기업에 장기 구조적 영향.
4점: 상당수 기업에 중기적 영향 예상.
3점: 일부 기업군에 영향 있으나 파급·지속성 제한.
2점: 특정 기업 단일 이슈.
1점: 홍보/행사 등 신용·구조와 무관.

[강제 규칙]
- 특정 기업 1곳 이슈면 최대 2점.
- 산업 전체 구조·규제·수급·경쟁/사이클이면 고점.

[기사 목록]
{prompt_list}

출력:
1번: 점수
2번: 점수
...
(설명 금지)
"""
    else:
        guideline = f"""
너는 신용평가사 애널리스트다. 아래 기사 제목/요약을 보고
대상 기업 "{target_keyword or 'N/A'}" 관점에서 신용영향 중요도를 1~5점으로 판단하라.

5점: 등급/전망 변경 가능성, 대규모 차입/조달, 유동성·회생/부도 등
4점: 대규모 투자·M&A·자산매각, 레버리지 급변, 유의미한 실적 변화
3점: 일반적 실적·구조조정·조달 이슈
2점: 영향 제한적 사업/마케팅/제휴
1점: 홍보/행사/ESG 등 신용 무관

[기사 목록]
{prompt_list}

출력:
1번: 점수
2번: 점수
...
(설명 금지)
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "사실 기반으로 점수화하라. 과장하지 말 것."},
                {"role": "user", "content": guideline},
            ],
            max_tokens=300,
            temperature=0
        )
        ans = resp.choices[0].message.content.strip()
    except Exception:
        return {i: 3 for i in range(len(articles))}

    score_map = {}
    for line in ans.splitlines():
        m = re.match(r"(\d+)번\s*:\s*([1-5])", line.strip())
        if m:
            no = int(m.group(1)) - 1
            score_map[no] = int(m.group(2))

    for i in range(len(articles)):
        score_map.setdefault(i, 3)

    return score_map


def llm_filter_and_rank_articles(main_kw, articles):
    if not articles:
        return articles

    cap = st.session_state.get("llm_candidate_cap", 200)
    top_k = st.session_state.get("llm_top_k", 10)

    main_kw_lower = (main_kw or "").lower()

    common_title_kws = ALL_COMMON_FILTER_KEYWORDS
    selected_industry_sub_kws = []
    if st.session_state.get("use_industry_filter", False):
        for sublist in st.session_state.industry_major_sub_map.values():
            selected_industry_sub_kws.extend(sublist)

    industry_credit_dict = parse_industry_credit_keywords()
    sector = get_sector_of_company(main_kw)
    sector_credit_kws = industry_credit_dict.get(sector, []) if sector else []

    def title_has_any_kw(title, kw_list):
        t = (title or "").lower()
        return any((kw or "").lower() in t for kw in kw_list if kw)

    p1, p2, p3 = [], [], []
    for a in articles:
        title = a.get("title", "") or ""
        t_lower = title.lower()

        if main_kw_lower and main_kw_lower in t_lower:
            p1.append(a); continue

        if (
            title_has_any_kw(title, common_title_kws) or
            title_has_any_kw(title, selected_industry_sub_kws) or
            title_has_any_kw(title, sector_credit_kws)
        ):
            p2.append(a); continue

        p3.append(a)

    def sort_newest(lst):
        return sorted(lst, key=lambda x: x.get("date", ""), reverse=True)

    p1, p2, p3 = sort_newest(p1), sort_newest(p2), sort_newest(p3)
    candidates = (p1 + p2 + p3)[:cap]

    scores = llm_score_articles_batch(candidates, target_keyword=main_kw)
    for i, a in enumerate(candidates):
        a["llm_score"] = scores.get(i, 3)
        if a in p1: a["rule_priority"] = 1
        elif a in p2: a["rule_priority"] = 2
        else: a["rule_priority"] = 3

    ranked = sorted(
        candidates,
        key=lambda x: (
            x.get("rule_priority", 3),
            -x.get("llm_score", 3),
            x.get("date", "")
        )
    )
    return ranked[:top_k]


def build_industry_major_article_pool(results_by_company):
    favorite_to_industry_major = config.get("favorite_to_industry_major", {})
    major_pool = {}
    industry_credit_dict = parse_industry_credit_keywords()

    for company, arts in results_by_company.items():
        majors = []
        for cat, comps in favorite_categories.items():
            if company in comps:
                majors.extend(favorite_to_industry_major.get(cat, []))
        majors = list(dict.fromkeys(majors))
        if not majors:
            continue

        for m in majors:
            sector_kws = industry_credit_dict.get(m, [])
            major_pool.setdefault(m, [])
            for a in arts:
                title = (a.get("title","") or "").lower()
                has_sector_kw = any(kw.lower() in title for kw in sector_kws)
                has_common_kw = any(kw.lower() in title for kw in ALL_COMMON_FILTER_KEYWORDS)
                pr = 1 if (has_sector_kw or has_common_kw) else 2
                major_pool[m].append({**a, "키워드": company, "industry_rule_priority": pr})

    for m in major_pool:
        major_pool[m] = sorted(
            major_pool[m],
            key=lambda x: (x.get("industry_rule_priority",3), x.get("date","")),
            reverse=False
        )
        if st.session_state.get("remove_duplicate_articles", False):
            major_pool[m] = remove_duplicates(major_pool[m])

    return major_pool


def llm_filter_and_rank_industry_major(major_name, articles):
    if not articles:
        return articles

    cap = st.session_state.get("industry_issue_cap", 300)
    top_k = st.session_state.get("industry_issue_top_k", 8)

    candidates = articles[:cap]
    scores = llm_score_articles_batch(candidates, target_keyword=major_name, mode="industry")
    for i, a in enumerate(candidates):
        a["llm_score"] = scores.get(i, 3)
        a["rule_priority"] = 2

    ranked = sorted(
        candidates,
        key=lambda x: (-x.get("llm_score", 3), x.get("date", ""))
    )
    return ranked[:top_k]


# =========================================================
# 7. 강력필터 fallback + 저장
# =========================================================
def process_keywords_with_synonyms(favorite_to_expand_map, start_date, end_date, require_keyword_in_title=False):
    for main_kw, kw_list in favorite_to_expand_map.items():
        all_articles = []
        did_fallback = False

        with ThreadPoolExecutor(max_workers=min(5, len(kw_list))) as executor:
            futures = {
                executor.submit(
                    fetch_naver_news,
                    search_kw,
                    start_date,
                    end_date,
                    require_keyword_in_title=require_keyword_in_title
                ): search_kw
                for search_kw in kw_list
            }
            for future in as_completed(futures):
                search_kw = futures[future]
                try:
                    fetched = future.result()
                    fetched = [{**a, "검색어": search_kw, "키워드": main_kw} for a in fetched]
                    all_articles.extend(fetched)
                except Exception as e:
                    st.warning(f"{main_kw} - '{search_kw}' 검색 실패: {e}")

        def passes_strong_filter_for_main(a):
            if st.session_state.get("require_exact_keyword_in_title_or_content", False):
                t = a.get("title", "") or ""
                d = a.get("description", "") or ""
                return (main_kw in t) or (main_kw in d)
            return True

        strong_main_articles = [a for a in all_articles if passes_strong_filter_for_main(a)]

        if (
            len(strong_main_articles) == 0
            and st.session_state.get("require_exact_keyword_in_title_or_content", False)
        ):
            did_fallback = True
            fallback_articles = []
            with ThreadPoolExecutor(max_workers=min(5, len(kw_list))) as executor:
                futures = {
                    executor.submit(
                        fetch_naver_news,
                        search_kw,
                        start_date,
                        end_date,
                        require_keyword_in_title=False
                    ): search_kw
                    for search_kw in kw_list
                }
                for future in as_completed(futures):
                    search_kw = futures[future]
                    try:
                        fetched = future.result()
                        fetched = [{**a, "검색어": search_kw, "키워드": main_kw} for a in fetched]
                        fallback_articles.extend(fetched)
                    except Exception as e:
                        st.warning(f"[Fallback] {main_kw} - '{search_kw}' 실패: {e}")
            all_articles = fallback_articles

        if st.session_state.get("remove_duplicate_articles", False):
            all_articles = remove_duplicates(all_articles)

        if st.session_state.get("use_llm_filter", False):
            all_articles = llm_filter_and_rank_articles(main_kw, all_articles)

        st.session_state.search_results[main_kw] = all_articles

        if main_kw not in st.session_state.show_limit:
            st.session_state.show_limit[main_kw] = 5


# =========================================================
# 8. 최종 필터(렌더 직전 공통)
# =========================================================
def or_keyword_filter(article, *keyword_lists):
    text = (article.get("title", "") + " " + article.get("description", "") + " " + article.get("full_text", ""))
    for keywords in keyword_lists:
        if any(kw in text for kw in keywords if kw):
            return True
    return False

def article_contains_exact_keyword(article, keywords, target_keyword=None):
    title = article.get("title", "") or ""
    content = ""
    link = article.get("link", "") or ""
    summary_key = get_summary_key_from_url(link, target_keyword)
    if summary_key in st.session_state and isinstance(st.session_state[summary_key], tuple):
        _, _, _, _, _, full_text = st.session_state[summary_key]
        content = full_text or ""

    for kw in keywords:
        if kw and (kw in title or (content and kw in content)):
            return True
    return False

def get_industry_majors_from_favorites(selected_categories):
    favorite_to_industry_major = config["favorite_to_industry_major"]
    majors = set()
    for cat in selected_categories:
        for major in favorite_to_industry_major.get(cat, []):
            majors.add(major)
    return list(majors)

def article_passes_all_filters(article):
    main_kw = (article.get("키워드") or "").strip()
    if not main_kw:
        return False

    main_kws = [main_kw] + SYNONYM_MAP.get(main_kw, [])
    title = article.get("title", "") or ""
    desc  = article.get("description", "") or ""
    text_short = f"{title} {desc}"
    company_mentioned = any(k in text_short for k in main_kws)

    if st.session_state.get("require_exact_keyword_in_title_or_content", False):
        if not company_mentioned:
            return False

    if exclude_by_title_keywords(title, EXCLUDE_TITLE_KEYWORDS):
        return False

    try:
        pub_date = datetime.strptime(article['date'], '%Y-%m-%d').date()
        if pub_date < st.session_state.get("start_date") or pub_date > st.session_state.get("end_date"):
            return False
    except:
        return False

    all_keywords = []
    if "keyword_input" in st.session_state:
        all_keywords.extend([k.strip() for k in st.session_state["keyword_input"].split(",") if k.strip()])
    if "cat_multi" in st.session_state:
        for cat in st.session_state["cat_multi"]:
            all_keywords.extend(favorite_categories.get(cat, []))

    keyword_passed = article_contains_exact_keyword(article, all_keywords, target_keyword=main_kw)

    if st.session_state.get("filter_allowed_sources_only", False):
        source = article.get('source', '').lower()
        if source.startswith("www."):
            source = source[4:]
        if source not in ALLOWED_SOURCES:
            return False

    common_passed = or_keyword_filter(article, ALL_COMMON_FILTER_KEYWORDS)
    if not common_passed:
        return False

    industry_passed = True
    if st.session_state.get("use_industry_filter", False):
        keyword = main_kw
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

    if not (industry_passed or keyword_passed):
        return False

    return True


# =========================================================
# 9. 중요기사 자동선정 / 엑셀
# =========================================================
def generate_important_article_list(search_results, common_keywords, industry_keywords, favorites):
    if not OPENAI_API_KEY or client is None:
        return []

    result = []
    ind_kw_by_cat = {cat: industry_keywords for cat in favorites.keys()}

    for category, companies in favorites.items():
        sector_keywords = ind_kw_by_cat.get(category, [])

        for comp in companies:
            articles = search_results.get(comp, [])
            if not articles:
                continue

            target_articles = []
            for a in articles:
                text = (a.get("title", "") + " " + a.get("description", "")).lower()
                has_sector = any(kw.lower() in text for kw in sector_keywords) if sector_keywords else True
                has_common = any(kw.lower() in text for kw in common_keywords) if common_keywords else True
                if has_sector and has_common:
                    target_articles.append(a)

            if not target_articles:
                continue

            prompt_list = "\n".join(
                [
                    f"{i+1}. [기업:{comp}] {a.get('title','')} || {a.get('description','')}"
                    for i, a in enumerate(target_articles)
                ]
            )

            guideline = f"""
당신은 신용평가사 애널리스트입니다.

[신용영향도 판단 기준]
5점: 신용등급/전망 변화 가능성, 대규모 자본확충·차입, 유동성 위기, 부도·회생, 중대한 규제·제재·소송 등
4점: 대규모 투자·M&A·지분매각, 실적 급변, 레버리지 급증, 계열사 위험 전이
3점: 일반적 실적 개선/악화, 중간 규모 조달
2점: 마케팅/제휴 등 영향 제한적
1점: 홍보/행사/ESG 등 신용 무관

[기사 목록]
{prompt_list}

분석 초점은 "{comp}"이며 "{category}" 산업 신용관점에서 평가하세요.

[지시사항]
1. 각 기사 번호별 점수(1~5점)
2. 5점 기사만 중요 후보
3. 5점 중 최대 2건 선정
4. 5점 없으면 '없음'

출력(설명 금지):
[평가]
1번: (점수)
...

[선정]
[중요1]: (기사번호 또는 없음)
[중요2]: (기사번호 또는 없음)
"""
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": guideline}],
                    max_tokens=900,
                    temperature=0.2,
                )
                answer = response.choices[0].message.content.strip()

                score_map = {}
                for line in answer.splitlines():
                    m = re.match(r"(\d+)번\s*:\s*([1-5])", line.strip())
                    if m:
                        no = int(m.group(1))
                        score_map[no] = int(m.group(2))

                sel1 = re.search(r"\[중요 ?1\]\s*:\s*(\d+)", answer)
                sel2 = re.search(r"\[중요 ?2\]\s*:\s*(\d+)", answer)

                raw_selected = []
                if sel1: raw_selected.append(int(sel1.group(1)))
                if sel2: raw_selected.append(int(sel2.group(1)))

                selected_idx0 = []
                for no in raw_selected:
                    idx0 = no - 1
                    if score_map.get(no) == 5 and 0 <= idx0 < len(target_articles):
                        if idx0 not in selected_idx0:
                            selected_idx0.append(idx0)

                if not selected_idx0:
                    continue

                for idx0 in selected_idx0:
                    a = target_articles[idx0]
                    result.append({
                        "키워드": comp,
                        "기사제목": a.get("title", ""),
                        "링크": a.get("link", ""),
                        "날짜": a.get("date", ""),
                        "출처": a.get("source", ""),
                        "감성": "",
                        "시사점": ""
                    })
            except Exception:
                continue

    return result


def matched_filter_keywords(article, common_keywords, industry_keywords):
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


def get_excel_download_with_favorite_and_excel_company_col(summary_data, favorite_categories, excel_company_categories, search_results):
    def clean_text(text):
        if not isinstance(text, str):
            text = str(text)
        text = text.replace('"', "'").replace('\n', ' ').replace('\r', '')
        return text[:200]

    sector_list = []
    for cat in favorite_categories:
        sector_list.extend(favorite_categories[cat])
    sector_list = list(dict.fromkeys(sector_list))

    excel_sector_list = []
    for cat in excel_company_categories:
        excel_sector_list.extend(excel_company_categories[cat])
    excel_sector_list = list(dict.fromkeys(excel_sector_list))

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

    implication_col = None
    if "한줄시사점" in df.columns:
        implication_col = "한줄시사점"
    elif "시사점" in df.columns:
        implication_col = "시사점"
    elif "implication" in df.columns:
        implication_col = "implication"

    keyword_col = "키워드" if "키워드" in df.columns else (df.columns[0] if len(df.columns) else "기업명")

    rows = []
    for idx, company in enumerate(sector_list):
        search_articles = search_results.get(company, [])
        filtered_articles = [a for a in search_articles if article_passes_all_filters(a)]

        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)

        if st.session_state.get("use_llm_filter", False):
            top_k = st.session_state.get("llm_top_k", 10)
            already_llm = (
                len(filtered_articles) <= top_k and
                all(("llm_score" in a) for a in filtered_articles)
            )
            if not already_llm:
                filtered_articles = llm_filter_and_rank_articles(company, filtered_articles)

        total_count = len(filtered_articles)

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
            implications[i] = getattr(art, implication_col, "") if implication_col else ""

        merged_implication = ""
        if implications[0]:
            merged_implication += f"1. {implications[0]}"
        if implications[1]:
            merged_implication += f"\n2. {implications[1]}"

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


# =========================================================
# 10. 신용평가 리포트 수집(KIS/NICE/KIE)  (기존 유지)
# =========================================================
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

def extract_credit_details(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    results = []
    items = soup.select('div.list li')
    for item in items:
        key_tag = item.find('dt') or item.find('strong')
        kind = key_tag.get_text(strip=True) if key_tag else None
        if not kind:
            continue

        grade_tag = item.find('span', string='등급')
        grade_val = ""
        if grade_tag:
            grade_node = grade_tag.find_next(['a', 'strong'])
            grade_val = grade_node.get_text(strip=True) if grade_node else ""

        outlook_tag = item.find('span', string=lambda s: s and ('Outlook' in s or 'Watchlist' in s))
        outlook_val = outlook_tag.next_sibling.strip() if outlook_tag and outlook_tag.next_sibling else ""

        eval_date_tag = item.find('span', string='평가일')
        eval_date_val = eval_date_tag.next_sibling.strip() if eval_date_tag and eval_date_tag.next_sibling else ""

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

def extract_reports_and_research(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, 'html.parser')
    result = {"평가리포트": [], "관련리서치": [], "신용등급상세": []}

    tables = soup.select('div.table_ty1 > table')
    for table in tables:
        caption = table.find('caption')
        if not caption:
            continue
        caption_text = caption.text.strip()

        def get_download_url(tr):
            for a in tr.find_all('a'):
                js_href = (a.get("href") or "") or (a.get("onclick") or "")
                url = extract_file_url(js_href)
                if url:
                    return url
            return ""

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
                download_url = get_download_url(tr)
                result["평가리포트"].append({
                    "종류": report_type,
                    "리포트": title,
                    "일자": date,
                    "평가종류": eval_type,
                    "다운로드": download_url
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
                download_url = get_download_url(tr)
                result["관련리서치"].append({
                    "구분": category,
                    "제목": title,
                    "일자": date,
                    "다운로드": download_url
                })

    result["신용등급상세"] = extract_credit_details(html_text)
    return result

def fetch_and_display_reports(companies_map):
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
            return {"major_grade_df": major_grade_df, "special_reports": special_reports}
        except Exception as e:
            return {"major_grade_df": pd.DataFrame(), "special_reports": [], "error": f"나이스 신용평가 데이터 로드 오류: {e}"}

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

            with st.expander(f"{company} (KISCD: {kiscd} | CMP_CD: {cmpcd} | KIE_CD: {kr_compcd})", expanded=False):
                st.markdown(
                    f"- [한국신용평가 (KIS)]({url_kis}) &nbsp;&nbsp; "
                    f"[나이스신용평가 (NICE)]({url_nice}) &nbsp;&nbsp; "
                    f"[한국기업평가 (KIE)]({url_kie})",
                    unsafe_allow_html=True
                )
                try:
                    resp = requests.get(url_kis, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        html_text = resp.text
                        report_data = extract_reports_and_research(html_text)

                        if report_data.get("평가리포트"):
                            with st.expander("평가리포트", expanded=True):
                                df_report = pd.DataFrame(report_data["평가리포트"])
                                st.dataframe(df_report)

                        if report_data.get("관련리서치"):
                            with st.expander("관련리서치", expanded=True):
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

                        credit_detail_list = extract_credit_details(html_text)
                        with st.expander("신용등급 상세정보", expanded=True):
                            if credit_detail_list:
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


# =========================================================
# 11. UI / 세션 init / CSS
# =========================================================
st.set_page_config(layout="wide")

def init_session_state():
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
        "industry_major_sub_map": {},
        "end_date": datetime.today().date(),
        "start_date": datetime.today().date() - timedelta(days=7),
        "remove_duplicate_articles": True,
        "require_exact_keyword_in_title_or_content": True,
        "filter_allowed_sources_only": False,
        "use_industry_filter": True,
        "show_sentiment_badge": False,
        "enable_summary": True,
        "keyword_input": "",
        "use_llm_filter": True,
        "llm_candidate_cap": 200,
        "llm_top_k": 10,
        "use_industry_issue_llm": True,
        "industry_issue_cap": 300,
        "industry_issue_top_k": 8,
        "search_run_id": 0,
        "industry_major_top_cache": {},
        "industry_major_top_cache_run_id": -1,

        # ✅ PATCH 핵심: 체크 상태를 단일 딕셔너리로 통합
        # key = f"{company}_{uid}"  value = True/False
        "selected_news": {},

        # 선택 동작 중복 rerun 방지용
        "last_toggle_key": "",
        "last_toggle_value": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session_state()

st.markdown("""
<style>
[data-testid="column"] > div { gap: 0rem !important; }
.stMultiSelect [data-baseweb="tag"] {
    background-color: #ff5c5c !important; color: white !important; border: none !important; font-weight: bold;
}
.sentiment-badge {
    display: inline-block; padding: 0.08em 0.6em; margin-left: 0.2em;
    border-radius: 0.8em; font-size: 0.85em; font-weight: bold; vertical-align: middle;
}
.sentiment-positive { background: #2ecc40; color: #fff; }
.sentiment-negative { background: #ff4136; color: #fff; }
.sentiment-neutral  { background: #6c757d; color: #fff; }
.stBox { background: #fcfcfc; border-radius: 0.7em; border: 1.5px solid #e0e2e6; margin-bottom: 1.2em;
    padding: 1.1em 1.2em 1.2em 1.2em; box-shadow: 0 2px 8px 0 rgba(0,0,0,0.03); }
.flex-row-bottom { display: flex; align-items: flex-end; gap: 0.5rem; margin-bottom: 0.5rem; }
.flex-grow { flex: 1 1 0%; }
.flex-btn { min-width: 90px; }
.news-title {
    word-break: break-all !important; white-space: normal !important; display: block !important; overflow: visible !important;
}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 12. 상단 입력 UI
# =========================================================
col_title, col_option1, col_option2 = st.columns([0.5, 0.2, 0.3])
with col_title:
    st.markdown(
        "<h1 style='color:#1a1a1a; margin-bottom:0.5rem;'>"
        "<a href='https://credit-issue-monitoring-news-sending.onrender.com/' target='_blank' style='text-decoration:none; color:#1a1a1a;'>"
        "📊 Credit Issue Monitoring</a></h1>",
        unsafe_allow_html=True
    )
with col_option1:
    st.checkbox("감성분석 배지표시", key="show_sentiment_badge")
with col_option2:
    st.checkbox("요약 기능", key="enable_summary")

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
        list(favorite_categories.keys()),
        key="cat_multi",
        label_visibility="collapsed"
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
    st.checkbox("이 필터 적용", key="use_industry_filter")
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

with st.expander("🔍 키워드 필터 옵션"):
    st.checkbox("키워드가 제목 또는 본문에 포함된 기사만 보기", key="require_exact_keyword_in_title_or_content")
    st.checkbox("중복 기사 제거", key="remove_duplicate_articles", help="키워드 검색 후 중복 기사를 제거합니다.")
    st.checkbox("특정 언론사만 검색", key="filter_allowed_sources_only", help="선택된 메이저 언론사만 필터링합니다.")

    st.checkbox(
        "LLM 중요도 필터 적용(전체 기업)",
        key="use_llm_filter",
        help="기업별 최신 cap건을 LLM이 1~5점 평가 후 상위 top_k만 보존"
    )
    st.number_input(
        "LLM 평가 후보 cap(최신순)",
        min_value=10, max_value=200, step=5,
        key="llm_candidate_cap"
    )
    st.number_input(
        "LLM 상위 기사 개수(top_k)",
        min_value=3, max_value=20, step=1,
        key="llm_top_k"
    )

    st.markdown("---")

    st.checkbox(
        "산업군별 주요이슈 LLM 필터 적용",
        key="use_industry_issue_llm",
        help="기업별 최종 기사들을 산업 대분류로 합쳐 top_k 선정"
    )
    st.number_input(
        "산업군별 LLM 후보 cap(최신순)",
        min_value=50, max_value=500, step=10,
        key="industry_issue_cap"
    )
    st.number_input(
        "산업군별 LLM 상위 기사 개수(top_k)",
        min_value=3, max_value=20, step=1,
        key="industry_issue_top_k"
    )


# =========================================================
# 13. 검색 실행
# =========================================================
keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()] if keywords_input else []

if search_clicked and keyword_list:
    with st.spinner("뉴스 검색 중..."):
        expanded = expand_keywords_with_synonyms(sorted(keyword_list))
        process_keywords_with_synonyms(
            expanded,
            st.session_state["start_date"],
            st.session_state["end_date"],
            require_keyword_in_title=st.session_state.get("require_exact_keyword_in_title_or_content", False)
        )
    st.session_state.search_run_id += 1

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
    st.session_state.search_run_id += 1


# =========================================================
# 14. 좌측/우측 렌더 (PATCH)
#    - 체크 상태 단일화(selected_news)
#    - 위젯 key를 항목별 고정
#    - 마스터 토글 1회만 rerun
# =========================================================
def render_articles_with_single_summary_and_telegram(results, show_limit, show_sentiment_badge=True, enable_summary=True):
    SENTIMENT_CLASS = {"긍정": "sentiment-positive", "부정": "sentiment-negative"}

    col_list, col_summary = st.columns([1, 1])

    # ------------------------------
    # 좌측: 뉴스 목록 + 체크
    # ------------------------------
    with col_list:
        st.markdown("### 🔍 뉴스 검색 결과")

        # (A) 산업 대분류 top_k
        if st.session_state.get("use_industry_issue_llm", True):
            if st.session_state.industry_major_top_cache_run_id != st.session_state.search_run_id:
                major_pool = build_industry_major_article_pool(results)
                cache = {}
                if major_pool:
                    with ThreadPoolExecutor(max_workers=min(8, len(major_pool))) as exe:
                        futures = {
                            exe.submit(llm_filter_and_rank_industry_major, major_name, major_articles): major_name
                            for major_name, major_articles in major_pool.items()
                        }
                        for fut in as_completed(futures):
                            major_name = futures[fut]
                            try:
                                cache[major_name] = fut.result()
                            except Exception:
                                cache[major_name] = []
                st.session_state.industry_major_top_cache = cache
                st.session_state.industry_major_top_cache_run_id = st.session_state.search_run_id

            cached_major_top = st.session_state.get("industry_major_top_cache", {})
            if cached_major_top:
                with st.expander("🟣 산업군별 주요 이슈(top_k)", expanded=True):
                    for major_name, major_top in cached_major_top.items():
                        with st.expander(f"🏭 {major_name} ({len(major_top)}건)", expanded=False):
                            for art in major_top:
                                company_tag = (art.get("키워드") or "").strip()
                                uid = make_uid(art["link"])
                                key = f"{company_tag}_{uid}" if company_tag else f"industry_{major_name}_{uid}"

                                # 고정 위젯 key
                                widget_key = f"major_chk_{key}"
                                checked = st.checkbox(
                                    "",
                                    value=st.session_state.selected_news.get(key, False),
                                    key=widget_key
                                )
                                st.session_state.selected_news[key] = checked

                                llm_info = f" | LLM점수:{art.get('llm_score')}점" if art.get("llm_score") else ""
                                company_info = f" | 기업:{company_tag}" if company_tag else ""
                                st.markdown(
                                    f"<span class='news-title'><a href='{art['link']}' target='_blank'>{art['title']}</a></span> "
                                    f"{art['date']} | {art['source']}{company_info}{llm_info}",
                                    unsafe_allow_html=True
                                )
            else:
                st.info("산업군별 주요 이슈를 만들 결과가 없습니다.")

        # (B) 기업/카테고리별 리스트
        for category_name, company_list in favorite_categories.items():
            companies_with_results = [c for c in company_list if c in results]
            if not companies_with_results:
                continue

            with st.expander(f"📂 {category_name}", expanded=True):
                for company in companies_with_results:
                    articles = results[company]
                    with st.expander(f"[{company}] ({len(articles)}건)", expanded=False):

                        all_article_keys = []
                        for art in articles:
                            uid = make_uid(art["link"])
                            key = f"{company}_{uid}"
                            all_article_keys.append(key)

                        current_key_set = set(all_article_keys)

                        # stale key 제거
                        for k in list(st.session_state.selected_news.keys()):
                            if k.startswith(f"{company}_") and k not in current_key_set:
                                st.session_state.selected_news.pop(k, None)

                        # 마스터 체크박스
                        slug = re.sub(r"\W+", "", f"{category_name}_{company}")
                        master_key = f"left_master_{slug}_select_all"

                        prev_value = all(st.session_state.selected_news.get(k, False) for k in all_article_keys)

                        select_all = st.checkbox(
                            f"전체 기사 선택/해제 ({company})",
                            value=prev_value,
                            key=master_key
                        )

                        if select_all != prev_value:
                            # 상태 반영
                            for k in all_article_keys:
                                st.session_state.selected_news[k] = select_all
                                # 개별 위젯도 즉시 반영되도록 key 값 갱신
                                st.session_state[f"left_chk_{k}"] = select_all
                            st.rerun()

                        # 개별 기사
                        for art in articles:
                            uid = make_uid(art["link"])
                            key = f"{company}_{uid}"
                            widget_key = f"left_chk_{key}"

                            checked = st.checkbox(
                                "",
                                value=st.session_state.selected_news.get(key, False),
                                key=widget_key
                            )
                            st.session_state.selected_news[key] = checked

                            cache_key = get_summary_key_from_url(art["link"], target_keyword=company)

                            sentiment = ""
                            if show_sentiment_badge and cache_key in st.session_state:
                                _, _, sentiment, _, _, _ = st.session_state[cache_key]

                            badge_html = (
                                f"<span class='sentiment-badge {SENTIMENT_CLASS.get(sentiment,'sentiment-neutral')}'>{sentiment}</span>"
                                if sentiment else ""
                            )

                            llm_info = f" | LLM점수:{art.get('llm_score')}점" if art.get("llm_score") else ""
                            search_kw = f" | 검색어:{art.get('검색어')}" if art.get('검색어') else ""

                            st.markdown(
                                f"<span class='news-title'><a href='{art['link']}' target='_blank'>{art['title']}</a></span> "
                                f"{badge_html} {art['date']} | {art['source']}{search_kw}{llm_info}",
                                unsafe_allow_html=True,
                            )

    # ------------------------------
    # 우측: 선택 기사 요약/감성
    # ------------------------------
    with col_summary:
        st.markdown("### 선택된 기사 요약/감성분석")
        with st.container(border=True):

            industry_keywords_all = []
            if st.session_state.get("use_industry_filter"):
                for sublist in st.session_state.industry_major_sub_map.values():
                    industry_keywords_all.extend(sublist)

            grouped_selected = {}

            # 기업 리스트 기준 선택 수집
            for cat_name, comp_list in favorite_categories.items():
                for company in comp_list:
                    if company in results:
                        for art in results[company]:
                            uid = make_uid(art["link"])
                            key = f"{company}_{uid}"
                            if st.session_state.selected_news.get(key, False):
                                grouped_selected.setdefault(cat_name, {}).setdefault(company, []).append((company, uid, art))

            # 산업군 major top에서 선택 수집
            cached_major_top = st.session_state.get("industry_major_top_cache", {})
            for major_name, major_top in cached_major_top.items():
                for art in major_top:
                    company = (art.get("키워드") or "").strip()
                    if not company:
                        continue
                    uid = make_uid(art["link"])
                    key = f"{company}_{uid}"
                    if st.session_state.selected_news.get(key, False):
                        grouped_selected.setdefault("산업군별 주요이슈", {}).setdefault(company, []).append((company, uid, art))

            # 요약 처리
            def process_article(item):
                company, uid, art = item
                cache_key = get_summary_key_from_url(art["link"], target_keyword=company)

                if cache_key in st.session_state:
                    one_line, summary, sentiment, implication, short_implication, full_text = st.session_state[cache_key]
                else:
                    one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
                        art["link"],
                        art["title"],
                        do_summary=enable_summary,
                        target_keyword=company,
                        description=art.get("description"),
                    )
                    st.session_state[cache_key] = (one_line, summary, sentiment, implication, short_implication, full_text)

                filter_hits = matched_filter_keywords(
                    {"title": art["title"], "요약본": summary, "요약": one_line, "full_text": full_text},
                    ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords_all,
                )

                return {
                    "키워드": company,
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
                    "full_text": full_text,
                }

            # 병렬 요약(선택된 것만)
            for cat_name, comp_map in grouped_selected.items():
                for company, items in comp_map.items():
                    with ThreadPoolExecutor(max_workers=8) as exe:
                        grouped_selected[cat_name][company] = list(exe.map(process_article, items))

            # 엑셀용 저장
            flattened = []
            for _cat, comp_map in grouped_selected.items():
                for _comp, arts in comp_map.items():
                    flattened.extend(arts)
            st.session_state.selected_articles = flattened

            # 렌더
            total_selected = 0
            for cat_name, comp_map in grouped_selected.items():
                with st.expander(f"📂 {cat_name}", expanded=True):
                    for company, arts in comp_map.items():
                        with st.expander(f"[{company}] ({len(arts)}건)", expanded=True):
                            for art in arts:
                                total_selected += 1
                                st.markdown(
                                    f"#### <a href='{art['링크']}' target='_blank'>{art['기사제목']}</a> "
                                    f"<span class='sentiment-badge {SENTIMENT_CLASS.get(art['감성'],'sentiment-neutral')}'>{art['감성']}</span>",
                                    unsafe_allow_html=True,
                                )
                                st.markdown(f"- **검색 키워드:** `{art['키워드']}`")
                                st.markdown(f"- **필터 히트:** `{art['필터히트'] or '없음'}`")
                                st.markdown(f"- **날짜/출처:** {art['날짜']} | {art['출처']}")
                                st.markdown(f"- **한 줄 요약:** {art['요약']}")
                                st.markdown(f"- **한 줄 시사점:** {art['한줄시사점']}")
                                st.markdown(f"- **시사점:** {art['시사점']}")
                                st.markdown("---")

            st.write(f"선택된 기사 개수: {total_selected}")

            col_dl1, col_dl2 = st.columns([0.55, 0.45])
            with col_dl1:
                st.download_button(
                    label="📥 맞춤 엑셀 다운로드",
                    data=get_excel_download_with_favorite_and_excel_company_col(
                        st.session_state.selected_articles,
                        favorite_categories,
                        excel_company_categories,
                        st.session_state.search_results,
                    ).getvalue(),
                    file_name="뉴스요약_맞춤형.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col_dl2:
                if st.button("🗑 선택 해제 (전체)"):
                    for k in list(st.session_state.selected_news.keys()):
                        st.session_state.selected_news[k] = False
                    # 개별 위젯도 동기화
                    for sk in list(st.session_state.keys()):
                        if sk.startswith("left_chk_") or sk.startswith("major_chk_"):
                            st.session_state[sk] = False
                    st.rerun()

        render_important_article_review_and_download()


# =========================================================
# 15. 중요기사 리뷰/다운로드 (기존 로직 유지 + rerun 통일)
# =========================================================
def render_important_article_review_and_download():
    import re
    from collections import defaultdict

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

                industry_keywords_all = []
                if st.session_state.get("use_industry_filter", False):
                    for sublist in st.session_state.industry_major_sub_map.values():
                        industry_keywords_all.extend(sublist)

                important_articles = generate_important_article_list(
                    search_results=filtered_results_for_important,
                    common_keywords=ALL_COMMON_FILTER_KEYWORDS,
                    industry_keywords=industry_keywords_all,
                    favorites=favorite_categories
                )
                for i, art in enumerate(important_articles):
                    important_articles[i] = {
                        "키워드": art.get("키워드") or "",
                        "기사제목": art.get("기사제목") or "",
                        "감성": art.get("감성", ""),
                        "링크": art.get("링크") or "",
                        "날짜": art.get("날짜") or "",
                        "출처": art.get("출처") or "",
                        "시사점": art.get("시사점", "")
                    }
                st.session_state["important_articles_preview"] = important_articles
                st.session_state["important_selected_index"] = []

        articles = st.session_state.get("important_articles_preview", [])
        selected_indexes = st.session_state.get("important_selected_index", [])

        major_map = defaultdict(lambda: defaultdict(list))
        for art in articles:
            keyword = art.get("키워드") or ""
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
                    cache_key = get_summary_key_from_url(link, target_keyword=minor)
                    if cache_key in st.session_state and isinstance(st.session_state[cache_key], tuple):
                        one_line_map[(major, minor, idx)] = st.session_state[cache_key]
                    else:
                        if link:
                            to_summarize.append((major, minor, idx, link, article.get("기사제목", "")))

        if to_summarize:
            with st.spinner("중요 기사 요약 생성 중..."):
                def get_one_line(args):
                    major, minor, idx, link, title = args
                    one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
                        link, title, do_summary=True, target_keyword=minor
                    )
                    cache_key = get_summary_key_from_url(link, target_keyword=minor)
                    st.session_state[cache_key] = (one_line, summary, sentiment, implication, short_implication, full_text)
                    return (major, minor, idx), (one_line, summary, sentiment, implication, short_implication, full_text)

                with ThreadPoolExecutor(max_workers=8) as executor:
                    for key, data_tuple in executor.map(get_one_line, to_summarize):
                        one_line_map[key] = data_tuple

        new_selection = []
        for major, minor_map in major_map.items():
            with st.expander(f"📊 {major}", expanded=True):
                for minor, arts in minor_map.items():
                    with st.expander(f"{minor} ({len(arts)}건)", expanded=False):
                        for idx, article in enumerate(arts):
                            uid = make_uid(article.get("링크",""))
                            check_key = f"important_chk_{major}_{minor}_{uid}"

                            cols = st.columns([0.06, 0.94])
                            with cols[0]:
                                checked = st.checkbox(
                                    "",
                                    key=check_key,
                                    value=((major, minor, idx) in selected_indexes),
                                )
                            with cols[1]:
                                st.markdown(
                                    f"{article.get('감성','')} | "
                                    f"<a href='{article.get('링크','')}' target='_blank'>"
                                    f"{article.get('기사제목','제목없음')}</a>",
                                    unsafe_allow_html=True,
                                )

                                summary_data = one_line_map.get((major, minor, idx))
                                implication_text = ""
                                short_implication_text = ""

                                if summary_data and len(summary_data) == 6:
                                    implication_text = summary_data[3] or ""
                                    short_implication_text = summary_data[4] or ""
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
                                st.markdown("<div style='margin:0px;padding:0px;height:4px'></div>", unsafe_allow_html=True)

                            if checked:
                                new_selection.append((major, minor, idx))

        st.session_state["important_selected_index"] = new_selection

        col_add, col_del, col_rep = st.columns([0.3, 0.35, 0.35])

        def extract_keyword_from_link(search_results, article_link):
            for kw, arts in search_results.items():
                for art in arts:
                    if art.get("link") == article_link:
                        return kw
            return ""

        with col_add:
            if st.button("➕ 선택 기사 추가"):
                left_selected_keys = [k for k, v in st.session_state.selected_news.items() if v]
                if not left_selected_keys:
                    st.warning("왼쪽 뉴스검색 결과에서 적어도 1개 이상 선택해 주세요.")
                else:
                    added_count = 0
                    important = st.session_state.get("important_articles_preview", [])

                    for from_key in left_selected_keys:
                        # from_key 형식: company_uid
                        m = re.match(r"^([^_]+)_(.+)$", from_key)
                        if not m:
                            continue
                        company = m.group(1)
                        uid_tail = m.group(2)

                        selected_article = None
                        for kw, arts in st.session_state.search_results.items():
                            for art in arts:
                                if make_uid(art["link"]) == uid_tail:
                                    selected_article = art
                                    break
                            if selected_article:
                                break
                        if not selected_article:
                            continue

                        keyword = extract_keyword_from_link(st.session_state.search_results, selected_article["link"])
                        cache_key = get_summary_key_from_url(selected_article["link"], target_keyword=keyword)

                        if cache_key in st.session_state:
                            sentiment = st.session_state[cache_key][2]
                        else:
                            _, _, sentiment, _, _, _ = summarize_article_from_url(
                                selected_article["link"],
                                selected_article["title"],
                                target_keyword=keyword
                            )

                        new_article = {
                            "키워드": keyword,
                            "기사제목": selected_article["title"],
                            "감성": sentiment or "",
                            "링크": selected_article["link"],
                            "날짜": selected_article["date"],
                            "출처": selected_article["source"],
                            "시사점": ""
                        }

                        if not any(a["링크"] == new_article["링크"] for a in important):
                            important.append(new_article)
                            added_count += 1

                        st.session_state.selected_news[from_key] = False

                    st.session_state["important_articles_preview"] = important
                    st.session_state["important_selected_index"] = []
                    if added_count > 0:
                        st.success(f"{added_count}건의 기사가 중요 기사 목록에 추가되었습니다.")
                    else:
                        st.info("추가된 새로운 기사가 없습니다.")
                    st.rerun()

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
                st.rerun()

        with col_rep:
            if st.button("🔁 선택 기사 교체"):
                left_selected_keys = [k for k, v in st.session_state.selected_news.items() if v]
                right_selected_indexes = st.session_state["important_selected_index"]
                if len(left_selected_keys) != 1 or len(right_selected_indexes) != 1:
                    st.warning("왼쪽 1개, 오른쪽 1개만 선택해주세요.")
                    return

                from_key = left_selected_keys[0]
                (target_major, target_minor, target_idx) = right_selected_indexes[0]

                m = re.match(r"^([^_]+)_(.+)$", from_key)
                if not m:
                    st.warning("기사 식별자 파싱 실패")
                    return
                uid_tail = m.group(2)

                selected_article = None
                for kw, art_list in st.session_state.search_results.items():
                    for art in art_list:
                        if make_uid(art["link"]) == uid_tail:
                            selected_article = art
                            break
                    if selected_article:
                        break
                if not selected_article:
                    st.warning("왼쪽에서 선택한 기사 정보를 찾을 수 없습니다.")
                    return

                keyword = extract_keyword_from_link(st.session_state.search_results, selected_article["link"])
                cache_key = get_summary_key_from_url(selected_article["link"], target_keyword=keyword)

                if cache_key in st.session_state:
                    sentiment = st.session_state[cache_key][2]
                else:
                    _, _, sentiment, _, _, _ = summarize_article_from_url(
                        selected_article["link"],
                        selected_article["title"],
                        target_keyword=keyword
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
                    "시사점": ""
                }
                important.append(new_article)

                st.session_state["important_articles_preview"] = important
                st.session_state.selected_news[from_key] = False
                st.session_state["important_selected_index"] = []
                st.success("중요 기사 교체 완료")
                st.rerun()

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
            cache_key = get_summary_key_from_url(link, target_keyword=keyword)
            if cache_key in st.session_state and isinstance(st.session_state[cache_key], tuple):
                one_line, summary, sentiment, implication, short_implication, full_text = st.session_state[cache_key]
            else:
                one_line, summary, sentiment, implication, short_implication, full_text = summarize_article_from_url(
                    link, raw_article.get("기사제목", ""), target_keyword=keyword
                )
                st.session_state[cache_key] = (one_line, summary, sentiment, implication, short_implication, full_text)

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
                "한줄시사점": short_implication,
                "링크": link,
                "날짜": raw_article.get("날짜", ""),
                "출처": raw_article.get("출처", ""),
                "full_text": full_text or "",
            }

        summary_data = [enrich_article_for_excel(a) for a in articles_source]

        def get_excel_with_joined_implications(summary_data, favorite_categories, excel_company_categories, search_results):
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
                filtered_articles = [a for a in search_articles if article_passes_all_filters(a)]
                if st.session_state.get("remove_duplicate_articles", False):
                    filtered_articles = remove_duplicates(filtered_articles)
                if st.session_state.get("use_llm_filter", False):
                    top_k = st.session_state.get("llm_top_k", 10)
                    already_llm = (
                        len(filtered_articles) <= top_k and
                        all(("llm_score" in a) for a in filtered_articles)
                    )
                    if not already_llm:
                        filtered_articles = llm_filter_and_rank_articles(company, filtered_articles)

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

        excel_data = get_excel_with_joined_implications(
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


# =========================================================
# 16. 렌더 직전 필터링 + 엔트리포인트
# =========================================================
if st.session_state.get("search_results"):
    filtered_results = {}
    top_k = st.session_state.get("llm_top_k", 10)

    for keyword, articles in st.session_state["search_results"].items():
        filtered_articles = [a for a in articles if article_passes_all_filters(a)]

        if st.session_state.get("remove_duplicate_articles", False):
            filtered_articles = remove_duplicates(filtered_articles)

        if st.session_state.get("use_llm_filter", False):
            already_llm = (
                len(filtered_articles) <= top_k and
                all(("llm_score" in a) for a in filtered_articles)
            )
            if not already_llm:
                filtered_articles = llm_filter_and_rank_articles(keyword, filtered_articles)

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

    kiscd_filtered = {c: kiscd_map[c] for c in selected_companies if c in kiscd_map}
    fetch_and_display_reports(kiscd_filtered)

else:
    st.info("뉴스 검색 결과가 없습니다. 먼저 검색을 실행해 주세요.")
