"""Perplexity 에이전트 API(Responses API)를 호출해서, 인용 출처가 달린 검색 결과를 받아오는 파일.

OpenAI의 web_search 도구는 검색 여부·횟수·깊이가 전적으로 모델 재량이라, 축이 3~4개인데
검색은 1~2번만 하고 넘어가서 근거가 부실해지는 문제가 있었다. 그 대신 여기서는 축마다
"이 질문에 대한 답을 찾아줘" 하고 명시적으로 검색을 위임해서, 실제로 인용 가능한 근거를
확보한다 — analysis_pipeline.py가 축 개수만큼 이 함수를 반복 호출한다.

주의: Perplexity는 2026년 8월 기준 API를 "Sonar 채팅완성"에서 "에이전트 API(/v1/responses)"로
개편했다. preset 이름도 예전 "fast-search"/"pro-search"에서 지금은 "fast"/"low"/"medium"/
"high"/"xhigh"/"wide-research"로 바뀌었다 — 이름이 또 바뀌면 여기와 PRESET 상수만 고치면 된다.
"""

import requests

PERPLEXITY_API_URL = "https://api.perplexity.ai/v1/responses"

# medium과 high는 둘 다 최대 15단계 다단계 브라우징을 하지만, high는 더 비싼 모델(GPT-5.6-Sol)을
# 써서 복잡한 질문에서 비용이 예측 불가능하게 튄다 — 실측 결과 같은 질문에 high는 $0.25,
# medium은 $0.01174로 22배 차이가 났는데 인용 품질(실제 출처 URL 개수·정확도)은 거의 같았다.
# 그래서 비용 예측 가능성을 위해 medium을 기본값으로 쓴다.
DEFAULT_PRESET = "medium"

# 응답에 usage.cost가 안 실려오는 경우를 대비한 대략적인 폴백 비용(달러/호출, 2026-08 기준
# 공식 가격 예시 페이지 기준 — 정확한 값은 항상 usage.cost를 우선 사용한다).
FALLBACK_COST_PER_CALL = {
    "fast": 0.0005,
    "low": 0.0008,
    "medium": 0.0018,
    "high": 0.0035,
    "xhigh": 0.007,
    "wide-research": 0.01,
}


def search(query: str, api_key: str, preset: str = DEFAULT_PRESET) -> dict:
    """query를 검색해서 {"query", "answer", "citations", "input_tokens", "output_tokens", "cost"}를
    돌려준다. citations는 [{"title", "url"}] 리스트, cost는 Perplexity가 직접 계산해준 달러 비용
    (usage.cost.total_cost) — 없으면 FALLBACK_COST_PER_CALL로 대략 추정한다.

    실패하면 requests의 HTTPError를 그대로 던진다 — 호출하는 쪽(analysis_pipeline.py)에서
    축 하나 실패로 전체 분석이 죽지 않게 처리한다.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"preset": preset, "input": query}
    response = requests.post(PERPLEXITY_API_URL, headers=headers, json=body, timeout=60)
    response.raise_for_status()
    result = response.json()

    # output 배열에는 두 종류의 항목이 섞여 온다 — type "search_results"(실제 검색된 URL·제목
    # 목록)와 type "message"(그 검색 결과를 종합한 최종 답변 텍스트). 답변 텍스트 안에는 "[4]"
    # 같은 인용 번호만 있고 URL 자체는 없어서, 인용 출처는 search_results 쪽에서 가져와야 한다.
    answer_parts = []
    citations = []
    seen_urls = set()
    for item in result.get("output", []):
        item_type = item.get("type")
        if item_type == "search_results":
            for r in item.get("results", []):
                url = r.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    citations.append({"title": r.get("title", ""), "url": url})
        elif item_type == "message":
            content = item.get("content")
            if isinstance(content, str):
                answer_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        answer_parts.append(part["text"])

    usage = result.get("usage", {})
    cost = (usage.get("cost") or {}).get("total_cost")
    if cost is None:
        cost = FALLBACK_COST_PER_CALL.get(preset, FALLBACK_COST_PER_CALL["high"])

    return {
        "query": query,
        "answer": "\n".join(answer_parts) or "(검색 결과에서 답을 찾지 못함)",
        "citations": citations,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost": cost,
        "preset": preset,
    }
