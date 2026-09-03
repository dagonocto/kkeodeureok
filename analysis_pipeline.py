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

import glossary
import perplexity_client
from feedback import load_feedback_notes
from prompts import (
    PLANNING_SCHEMA,
    PLANNING_SYSTEM_PROMPT,
    REVIEW_SCHEMA,
    REVIEW_SYSTEM_PROMPT,
    WRITER_SCHEMA,
    WRITER_SYSTEM_PROMPT,
)
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
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0, stage="plan")
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
        print(f"[perplexity] 검색 실패 (query={axis_plan['research_query']!r}): {e}")
        # 원본 예외 메시지(HTTP 상태 코드 등)를 그대로 넣으면 작성 단계가 "429 오류" 같은
        # 내부 사정을 독자 대상 설명에 그대로 옮겨 적는 경우가 있었다 — 일반 독자용 문구로 남긴다.
        result = {"query": axis_plan["research_query"], "answer": "이번 검색에서는 관련 자료를 확인하지 못했다.", "citations": []}
        cost = 0.0
    return result, cost


def _research(
    plan: dict, perplexity_api_key: str, notion_token: str | None, glossary_data_source_id: str | None
) -> tuple[list[dict], dict[str, str], float]:
    """2단계: 축마다 research_query를 확인한다.

    "용어 뽀개기" 축은 먼저 용어사전(Notion)에 이미 정의가 있는지 본다 — 있으면 검색 없이
    재사용(비용 절감 + 같은 용어를 매번 다르게 설명하는 일 방지). 없는 축들만 Perplexity로
    동시에 검색한다. 용어사전이 설정 안 돼 있으면(notion_token/glossary_data_source_id가
    없으면) 그냥 매번 검색하는 예전 방식으로 동작한다.

    저장은 여기서 하지 않는다 — 검색 결과가 실제로 쓸 만한지는 작성 단계가 끝나야 알 수
    있어서(예: 근거 부실해서 축 자체를 버리는 경우), 새로 검색된 (term, 정의) 후보만
    fresh_terms로 돌려주고, 실제 저장 여부는 write 이후 _save_new_glossary_terms가 판단한다.
    """
    axes = plan["axes"]
    glossary_enabled = bool(notion_token and glossary_data_source_id)
    findings: list[dict | None] = [None] * len(axes)
    to_search = []

    for i, axis_plan in enumerate(axes):
        term = axis_plan.get("glossary_term")
        if glossary_enabled and axis_plan["family"] == "용어 뽀개기" and term:
            cached = glossary.lookup(term, notion_token, glossary_data_source_id)
            if cached:
                findings[i] = {"query": axis_plan["research_query"], "answer": cached, "citations": []}
                continue
        to_search.append(i)

    total_cost = 0.0
    if to_search:
        # (실험 중 — 되돌릴 수 있음) 예전엔 동시 실행 수를 2로 낮춰서 429를 줄였는데,
        # 그 대가로 축이 3~4개면 검색이 두 번(순차)으로 나뉘어 전체 시간이 거의 2배로 늘어난다.
        # 지금은 429가 나면 perplexity_client.search 안에서 자동으로 재시도(대기 후 재요청)
        # 하므로, 순간적으로 429가 나더라도 완전히 실패하지는 않는다 — 그래서 축 개수(최대
        # 4개)만큼 동시에 쏴서 전체 시간을 줄여본다. 실측해서 429가 잦아지면 다시 낮춘다.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(to_search))) as executor:
            searched = list(executor.map(lambda i: _research_one(axes[i], perplexity_api_key), to_search))
        for i, (result, cost) in zip(to_search, searched):
            findings[i] = result
            total_cost += cost

    fresh_terms = {}
    if glossary_enabled:
        for i in to_search:
            axis_plan = axes[i]
            term = axis_plan.get("glossary_term")
            if axis_plan["family"] == "용어 뽀개기" and term:
                fresh_terms[term] = findings[i]["answer"]

    return findings, fresh_terms, total_cost


def _save_new_glossary_terms(
    written_axes: list[dict], fresh_terms: dict[str, str], notion_token: str, glossary_data_source_id: str
) -> None:
    """새로 검색된 용어 중, 작성 단계가 실제로 채택하고 confidence를 "low"로 두지 않은
    것만 용어사전에 저장한다. 근거 부실해서 작성 단계가 버린 축이나, 확신 없다고 표시한
    정의는 저장하지 않는다 — 틀렸을 수도 있는 내용이 그대로 굳어버리는 걸 막기 위해서다.
    """
    kept_terms = {
        axis["glossary_term"]
        for axis in written_axes
        if axis["family"] == "용어 뽀개기" and axis.get("glossary_term") and axis["confidence"] != "low"
    }
    for term, definition in fresh_terms.items():
        if term in kept_terms:
            glossary.save(term, definition, notion_token, glossary_data_source_id)


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
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0, stage="write")
    written = strip_trailing_artifacts(json.loads(response.output_text))
    # 스키마는 문단마다 body를 먼저 쓰고 heading을 나중에 쓰게 강제한다(JSON 필드 순서대로
    # 채워지므로) — heading을 먼저 정해두고 거기 맞춰 body를 쓰다가 내용이 미묘하게 어긋나는
    # 문제를 줄이기 위해서다. 여기서 그 paragraphs를 하나의 explanation 문자열로 합친다 —
    # 이후 파이프라인(검토 단계, Notion 저장, 화면 표시)은 전부 explanation 문자열 하나만 안다.
    for axis in written["axes"]:
        axis["explanation"] = "\n\n".join(
            f"**{p['heading']}**\n{p['body']}" for p in axis.pop("paragraphs")
        )
    # 지침상 "뺄 축은 결과 배열에 아예 포함하지 않는다"고 되어 있지만, 모델이 이걸 안 지키고
    # explanation을 빈 문자열로만 남겨두는 경우가 있어서 여기서도 한 번 더 걸러낸다.
    written["axes"] = [axis for axis in written["axes"] if axis["explanation"].strip()]
    return written, cost


def _review(client: OpenAI, axes: list[dict]) -> tuple[list[dict], float]:
    """4단계(검토): 이미 작성된 카드끼리 겹치는 내용이 있으면 정리한다.

    작성 단계 지침 안에 "겹치면 정리해라"는 일반 규칙을 계속 넣어왔는데도 매번 새로운
    형태로 카드 중복이 반복됐다 — 이 검토만 전담으로 하는 별도 단계가 훨씬 안정적으로
    지켜질 거라는 판단으로 추가했다. 검색이 필요 없는 순수 텍스트 재검토라 비용·시간
    부담이 작다. 카드가 1개뿐이면 겹칠 상대가 없으므로 호출 자체를 건너뛴다.
    """
    if len(axes) < 2:
        return axes, 0.0

    axes_text = json.dumps(axes, ensure_ascii=False, indent=2)
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=16000,
        input=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": f"아래 카드들을 검토해줘.\n\n{axes_text}"},
        ],
        text={"format": {"type": "json_schema", "name": "axes_review", "strict": True, "schema": REVIEW_SCHEMA}},
    )
    if response.status != "completed":
        # 검토 단계가 실패해도 이미 작성된 카드는 멀쩡하다 — 검토를 건너뛰고 원본을 그대로 쓴다.
        return axes, 0.0
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0, stage="review")
    reviewed = json.loads(response.output_text)["axes"]
    reviewed = [axis for axis in reviewed if axis["explanation"].strip()]
    return reviewed, cost


def analyze_article(
    document_block: dict,
    url: str,
    openai_api_key: str,
    perplexity_api_key: str,
    notion_token: str | None = None,
    glossary_data_source_id: str | None = None,
    on_stage: callable = None,
) -> tuple[dict, float]:
    """기사 하나를 분석한다. (기획 → 조사 → 작성 → 검토), 결과와 총 비용을 돌려준다.

    notion_token/glossary_data_source_id는 선택值이다 — 주면 "용어 뽀개기" 축에 용어사전
    캐시를 쓰고, 안 주면(예: 아직 사전을 안 만들었거나 회귀 테스트) 매번 검색하는 예전
    방식 그대로 동작한다.

    on_stage: 단계가 끝날 때마다 "plan"/"research"/"write"/"review"를 인자로 호출되는
    콜백(선택). app.py가 이걸로 진행률 표시줄을 "이 단계는 실제로 끝났다"는 진짜
    체크포인트에 맞춰 보여준다 — API 호출 하나의 내부 진행률까지는 알 수 없지만, 네
    단계 경계는 실제로 관측 가능하다.
    """
    client = OpenAI(api_key=openai_api_key)

    plan, plan_cost = _plan(client, document_block, url)
    if on_stage:
        on_stage("plan")
    findings, fresh_terms, research_cost = _research(plan, perplexity_api_key, notion_token, glossary_data_source_id)
    if on_stage:
        on_stage("research")
    written, write_cost = _write(client, document_block, plan, findings)
    if on_stage:
        on_stage("write")
    reviewed_axes, review_cost = _review(client, written["axes"])
    if on_stage:
        on_stage("review")

    if fresh_terms and notion_token and glossary_data_source_id:
        _save_new_glossary_terms(reviewed_axes, fresh_terms, notion_token, glossary_data_source_id)

    # plan에도 "axes"가 있지만 research_query만 있는 기획 단계 버전이라, 검토까지 끝난
    # 완성된 axes로 덮어쓴다.
    data = {**plan, "axes": reviewed_axes, "references": written["references"]}

    # 모델이 언론사명을 못 찾으면 "확인 불가"를 돌려주는데, 그러면 Notion 페이지에
    # 출처가 아예 안 보인다. URL의 도메인이라도 있으면 그걸로 대신 채운다.
    if data.get("source_name") in (None, "", "확인 불가") and url:
        domain = urlparse(url).netloc.removeprefix("www.")
        if domain:
            data["source_name"] = domain

    total_cost_usd = plan_cost + research_cost + write_cost + review_cost
    return data, total_cost_usd
