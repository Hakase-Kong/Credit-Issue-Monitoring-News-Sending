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
        if not articles:
            st.info("자동선정된 중요 기사가 없습니다. 필터 기준 또는 선정 프롬프트/파싱 코드를 점검해주세요.")
            return
        selected_indexes = st.session_state.get("important_selected_index", [])

        st.markdown("🎯 **중요 기사 목록** (키워드별 분류, 교체/삭제/추가 반영)")

        from collections import defaultdict
        grouped = defaultdict(list)
        for idx, article in enumerate(articles):
            kw = article.get("키워드") or article.get("회사명") or "기타"
            grouped[kw].append((idx, article))
        ordered_keywords = list(favorite_categories.keys())
        shown_keywords = [kw for kw in ordered_keywords if kw in grouped]
        etc_keywords = [kw for kw in grouped if kw not in shown_keywords]
        all_keywords = shown_keywords + sorted(etc_keywords)

        for kw in all_keywords:
            items = grouped[kw]
            with st.expander(f"[{kw}] ({len(items)}건)", expanded=False):

                # 병렬 요약처리
                from concurrent.futures import ThreadPoolExecutor

                def summarize_for_render(idx_and_art):
                    idx, article = idx_and_art
                    cleaned_id = re.sub(r"\W+", "", article.get("링크", ""))[-16:]
                    summary_key = f"summary_{cleaned_id}"
                    if summary_key in st.session_state and type(st.session_state[summary_key]) is tuple:
                        one_line = st.session_state[summary_key][0]
                    else:
                        try:
                            one_line, *_ = summarize_article_from_url(
                                article.get("링크", ""), article.get("기사제목", ""),
                                do_summary=True, target_keyword=article.get("키워드", "")
                            )
                            st.session_state[summary_key] = (one_line, None, None, None)
                        except Exception:
                            one_line = ""
                    return idx, article, one_line

                item_list = [(idx, article) for idx, article in items]
                with ThreadPoolExecutor(max_workers=8) as executor:
                    summarized_results = list(executor.map(summarize_for_render, item_list))

                # 결과 일괄 렌더링
                for idx, article, one_line in summarized_results:
                    col_checkbox, col_label = st.columns([0.04, 0.96], gap="small")
                    with col_checkbox:
                        cb = st.checkbox('', key=f"important_chk_{idx}", value=(idx in selected_indexes))
                    with col_label:
                        label = (
                            f"{article.get('감성', '')} | "
                            f"<a href='{article.get('링크')}' target='_blank'>{article.get('기사제목', '')}</a>"
                        )
                        st.markdown(label, unsafe_allow_html=True)
                    if one_line and one_line != "요약 추출 실패":
                        st.markdown(
                            f"<span style='color:gray;font-style:italic;font-size:0.94em'>{one_line}</span>",
                            unsafe_allow_html=True
                        )
                    st.write("")  # 얇은 줄

                    # 체크상태 동기화
                    if cb:
                        if idx not in selected_indexes:
                            selected_indexes.append(idx)
                    else:
                        if idx in selected_indexes:
                            selected_indexes.remove(idx)

        st.session_state["important_selected_index"] = selected_indexes

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

        # --- 엑셀 다운로드 ---
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
