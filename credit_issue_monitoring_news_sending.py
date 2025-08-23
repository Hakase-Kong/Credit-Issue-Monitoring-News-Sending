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

            # (1) 최대한 많은 필드에서 키워드 매칭 시도 (title, description, 본문 등)
            target_articles = [
                a for a in articles if any(
                    kw in (
                        a.get("title","") +
                        " " + a.get("description","") +
                        " " + a.get("full_text","") +
                        " " + a.get("요약본", "") +
                        " " + a.get("요약", "")
                    ) for kw in filtered_keywords if kw
                )
            ]

            # (2) 만약 후보군이 3건 미만이면, 그냥 상위 10건이라도 fallback (최신순)
            if len(target_articles) < 3:
                # 중복 없이 articles 전체에서 상위 10건만
                seen_links = set(a["link"] for a in target_articles)
                fallback_articles = [a for a in articles if a["link"] not in seen_links]
                target_articles += fallback_articles[:max(0, 10 - len(target_articles))]

            # (3) 여전히 후보가 없다? continue
            if not target_articles:
                continue

            prompt_list = "\n".join([f"{i+1}. {a['title']} - {a['link']}" for i, a in enumerate(target_articles)])
            prompt = (
                f"[기사 목록]\n{prompt_list}\n\n"
                "아래 기업(또는 키워드)의 신용, 실적, 리스크 변화와 관련해서 '특히 주목할 만한' 기사를 최대 3건까지 선정해 주세요.\n"
                "- 긍정적 변화(재무 안정, 실적 호전 등), 부정적 변화(리스크 확대, 실적 악화 등), 참고할 변화(중립적 이슈 등)로 한 건씩(없으면 생략)\n"
                "- 전체 맥락에서 투자자·신용분석자 입장에서 '뉴스 중요도' 위주로\n"
                "- 너무 사소한 기사, 단순 사실 재언급 등은 제외\n\n"
                "[선정 기사 예시]\n"
                "[긍정]:(기사 제목)\n"
                "[부정]:(기사 제목)\n"
                "[참고]:(기사 제목)\n"
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=900,
                    temperature=0,
                )
                answer = response.choices[0].message.content.strip()
                pos_title = re.search(r"\[긍정\]:\s*(.+)", answer)
                neg_title = re.search(r"\[부정\]:\s*(.+)", answer)
                neu_title = re.search(r"\[참고\]:\s*(.+)", answer)

                pos_title = pos_title.group(1).strip() if pos_title else ""
                neg_title = neg_title.group(1).strip() if neg_title else ""
                neu_title = neu_title.group(1).strip() if neu_title else ""

                # (4) 선정 결과 반영
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
                    if neu_title and neu_title in a["title"]:
                        result.append({
                            "회사명": comp,
                            "감성": "참고",
                            "제목": a["title"],
                            "링크": a["link"],
                            "날짜": a["date"],
                            "출처": a["source"]
                        })
            except Exception:
                continue
    return result
