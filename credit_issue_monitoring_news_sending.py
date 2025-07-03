import streamlit as st
import requests

# --- RapidAPI 설정 ---
API_URL = "https://article-extractor-and-summarizer.p.rapidapi.com/summarize"
API_KEY = "3558ef6abfmshba1bd48265c6fc4p101a63jsnb2c1ee3d33c4"
API_HOST = "article-extractor-and-summarizer.p.rapidapi.com"

# --- Streamlit UI ---
st.set_page_config(page_title="뉴스 기사 요약기", layout="centered")
st.title("📰 뉴스 기사 요약기 (RapidAPI)")

url = st.text_input("기사 URL을 입력하세요:")

if url:
    st.info("기사 내용을 불러오고 요약 중입니다...")

    querystring = {
        "url": url,
        "lang": "en",       # 필요시 'ko'로 변경 (한글 기사일 경우)
        "engine": "2"       # 1 = 기본 요약기, 2 = 향상된 요약기
    }

    headers = {
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": API_HOST
    }

    try:
        response = requests.get(API_URL, headers=headers, params=querystring)
        response.raise_for_status()
        result = response.json()

        st.subheader("📄 기사 원문")
        st.write(result.get("text", "본문 추출 실패"))

        st.subheader("📝 요약 결과")
        st.write(result.get("summary", "요약 결과 없음"))

    except Exception as e:
        st.error(f"API 호출 중 오류가 발생했습니다: {e}")
