"""기사 분석의 실제 로직 — 기획(OpenAI) → 조사(Perplexity) → 작성(OpenAI) 3단계.

app.py(Streamlit 웹앱)와 regression_test.py(회귀 테스트) 양쪽에서 이 로직이 갈라지면
나중에 한쪽만 고치고 잊어버리는 일이 생기기 쉬워서, 실제 분석 파이프라인은 여기 하나에만
두고 양쪽에서 그대로 가져다 쓴다.

왜 3단계로 나눴나: OpenAI의 web_search 도구는 검색 여부·횟수·깊이가 전적으로 모델
재량이라, 축이 3~4개인데 검색은 1~2번만 하고 넘어가서 근거가 부실해지는 문제가 있었다.
그래서 "어떤 축이 필요하고 뭘 검색해야 하는지 기획"(planning)과 "축마다 실제로 검색해서
얻은 근거를 바탕으로 설명을 작성"(writer)을 분리하고, 그 사이에 축 개수만큼 Perplexity
검색을 강제로 수행한다.
"""

import concurrent.futures
import json
from urllib.parse import urlparse

from openai import OpenAI

import perplexity_client
from feedback import load_feedback_notes
from prompts import PLANNING_SCHEMA, PLANNING_SYSTEM_PROMPT, WRITER_SCHEMA, WRITER_SYSTEM_PROMPT
from text_cleanup import strip_trailing_artifacts
from usage_log import log_perplexity_usage, log_usage

MODEL = "gpt-5.4-mini"


def _with_feedback(base_prompt: str) -> str:
    """기본 지침 + 지금까지 쌓인 사용자 피드백(lessons.md)을 합쳐서 최종 지침을 만든다."""
    notes = load_feedback_notes()
    if not notes:
        return base_prompt
    return f"{base_prompt}\n\n## 사용자가 남긴 과거 피드백 (반드시 참고해서 같은 실수를 반복하지 말 것)\n{notes}"


def _plan(client: OpenAI, document_block: dict, url: str) -> tuple[dict, float]:
    """1단계: 어떤 축이 필요하고, 축마다 뭘 검색해야 하는지 정한다. (검색 없음, 저렴함)"""
    instruction = f"이 기사 원문을 분석해줘. 원문 URL: {url if url else '없음(파일로만 제공됨)'}"
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=6000,
        input=[
            {"role": "system", "content": _with_feedback(PLANNING_SYSTEM_PROMPT)},
            {"role": "user", "content": [document_block, {"type": "input_text", "text": instruction}]},
        ],
        text={"format": {"type": "json_schema", "name": "article_plan", "strict": True, "schema": PLANNING_SCHEMA}},
    )
    if response.status != "completed":
        raise RuntimeError(f"기획 단계가 끝까지 완료되지 않았어요 (status={response.status})")
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0)
    return json.loads(response.output_text), cost


def _research_one(axis_plan: dict, perplexity_api_key: str) -> tuple[dict, float]:
    """축 하나의 research_query를 Perplexity로 검색한다. 실패해도 예외를 던지지 않고
    "조사 실패"로 표시해서 돌려준다 — 축 하나가 실패해도 전체 분석이 죽지 않도록.
    """
    try:
        result = perplexity_client.search(axis_plan["research_query"], perplexity_api_key)
        cost = log_perplexity_usage(
            result["input_tokens"], result["output_tokens"], result["cost"], result["preset"]
        )
    except Exception as e:  # noqa: BLE001 - 검색 실패 축 하나 때문에 전체를 죽이지 않는다
        result = {"query": axis_plan["research_query"], "answer": f"(조사 실패: {e})", "citations": []}
        cost = 0.0
    return result, cost


def _research(plan: dict, perplexity_api_key: str) -> tuple[list[dict], float]:
    """2단계: 축마다 Perplexity로 research_query를 검색한다 — 축 개수만큼 동시에 실행해서,
    순서대로 기다릴 때보다 전체 소요 시간을 크게 줄인다(가장 느린 검색 1건 시간에 수렴).
    """
    axes = plan["axes"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(axes)) as executor:
        results = list(executor.map(lambda ap: _research_one(ap, perplexity_api_key), axes))
    findings = [r for r, _ in results]
    total_cost = sum(c for _, c in results)
    return findings, total_cost


def _write(client: OpenAI, document_block: dict, plan: dict, findings: list[dict]) -> tuple[dict, float]:
    """3단계: 원문 + 조사 결과를 근거로 실제 explanation/talk_line/references를 쓴다."""
    findings_text = "\n\n".join(
        f"[축 {i + 1}: {axis_plan['title']} ({axis_plan['family']})]\n"
        f"검색 질문: {axis_plan['research_query']}\n"
        f"조사 결과: {finding['answer']}\n"
        f"출처: {', '.join(c['url'] for c in finding['citations'] if c.get('url')) or '없음'}"
        for i, (axis_plan, finding) in enumerate(zip(plan["axes"], findings))
    )
    writer_instruction = (
        "기사 원문과 아래 조사 결과를 바탕으로 각 축의 설명을 작성해줘.\n\n"
        f"### 기사 요약\n{chr(10).join('- ' + s for s in plan['summary'])}\n\n"
        f"### 조사 결과\n{findings_text}"
    )
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=24000,
        input=[
            {"role": "system", "content": _with_feedback(WRITER_SYSTEM_PROMPT)},
            {"role": "user", "content": [document_block, {"type": "input_text", "text": writer_instruction}]},
        ],
        text={"format": {"type": "json_schema", "name": "article_writeup", "strict": True, "schema": WRITER_SCHEMA}},
    )
    if response.status != "completed":
        raise RuntimeError(f"작성 단계가 끝까지 완료되지 않았어요 (status={response.status})")
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0)
    written = strip_trailing_artifacts(json.loads(response.output_text))
    return written, cost


def analyze_article(document_block: dict, url: str, openai_api_key: str, perplexity_api_key: str) -> tuple[dict, float]:
    """기사 하나를 분석한다. (기획 → 조사 → 작성), 결과와 총 비용을 돌려준다."""
    client = OpenAI(api_key=openai_api_key)

    plan, plan_cost = _plan(client, document_block, url)
    findings, research_cost = _research(plan, perplexity_api_key)
    written, write_cost = _write(client, document_block, plan, findings)

    # plan에도 "axes"가 있지만 research_query만 있는 기획 단계 버전이라, written의 완성된
    # axes로 덮어쓴다.
    data = {**plan, "axes": written["axes"], "references": written["references"]}

    # 모델이 언론사명을 못 찾으면 "확인 불가"를 돌려주는데, 그러면 Notion 페이지에
    # 출처가 아예 안 보인다. URL의 도메인이라도 있으면 그걸로 대신 채운다.
    if data.get("source_name") in (None, "", "확인 불가") and url:
        domain = urlparse(url).netloc.removeprefix("www.")
        if domain:
            data["source_name"] = domain

    total_cost_usd = plan_cost + research_cost + write_cost
    return data, total_cost_usd
