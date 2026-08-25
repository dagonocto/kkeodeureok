"""새로 분석한 기사가 최근 저장된 기사들 중 하나와 이어지는 사안인지 판단하는 파일.

이어진다고 판단되면 두 기사에 같은 "연관 시리즈" 이름을 붙인다 — app.py 첫 화면이
같은 이름이 붙은 기사들을 모아 타임라인으로 보여주는 데 이 값을 쓴다.

판단은 여기서 하지만 저장(Notion 속성 쓰기)은 notion_client.py에 맡긴다 — 이 파일은
"어떤 이름을 붙일지"만 정하고, 실제 쓰기는 build_properties(새 기사)와
set_thread_name(예전 기사 소급 반영)이 담당한다.
"""

import json

from openai import OpenAI

import notion_client
from usage_log import log_usage

MODEL = "gpt-5.4-mini"

SYSTEM_PROMPT = """너는 방금 분석된 기사 하나가, 최근 저장된 기사들 중 하나와 시간차를
두고 이어지는 같은 사안인지 판단하는 에이전트다.

"이어진다"는 건 단순히 같은 지역·기관·인물이 등장한다는 뜻이 아니다. 하나의 사안이
전개되면서 뒤 기사가 앞 기사의 후속·원인·결과·다음 단계인 경우만 해당한다. 예를 들어
"경기도 재정 비상 선언"과 몇 주 뒤 "그 여파로 인한 경기도 인사 보류 논란"은 이어지는
사안이지만, 그냥 "경기도"라는 이유만으로 서로 무관한 두 기사를 묶으면 안 된다.

무리해서 연결하지 마라 — 확신이 없으면 이어지지 않는다고 답한다. 후보 목록에 정말
이어지는 기사가 없으면 matched를 false로만 두고 나머지 필드는 채우지 않는다.

이어진다고 판단되면:
- 매칭된 기존 기사에 이미 연관 시리즈 이름이 있으면, 그 이름을 정확히 그대로(한 글자도
  바꾸지 않고) thread_name에 반환한다 — 그래야 나중에 같은 그룹으로 묶인다.
- 매칭된 기존 기사에 아직 이름이 없으면, 두 기사를 모두 아우르는 짧은 새 이름을 만든다
  (5~10자, 예: "경기도 재정위기", "중수청 출범", "배재고 5·18 조롱 논란"). 이때
  matched_page_needs_backfill을 true로 표시한다.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matched", "thread_name", "matched_page_id", "matched_page_needs_backfill"],
    "properties": {
        "matched": {"type": "boolean"},
        "thread_name": {"type": ["string", "null"], "description": "이어진다고 판단됐을 때만 채움, 아니면 null"},
        "matched_page_id": {"type": ["string", "null"], "description": "이어진다고 판단된 기존 기사의 id, 아니면 null"},
        "matched_page_needs_backfill": {
            "type": "boolean",
            "description": "매칭된 기존 기사에 연관 시리즈 이름이 없어서 이번에 새로 붙여주는 경우 true",
        },
    },
}


def assign_thread(
    openai_api_key: str, data: dict, notion_token: str, data_source_id: str
) -> tuple[str | None, float]:
    """최근 기사들과 비교해서 이 기사에 붙일 연관 시리즈 이름을 정한다.

    이어지는 기존 기사가 있고 그 기사에 아직 이름이 없었다면, 그 기사에도 같은 이름을
    소급해서 붙인다(notion_client.set_thread_name). 실패해도 예외를 던지지 않는다 —
    이 판단이 실패한다고 기사 저장 자체가 막히면 안 된다.
    """
    try:
        recent = notion_client.list_recent_pages(notion_token, data_source_id, limit=40)
    except Exception:  # noqa: BLE001 - 조회 실패해도 저장은 계속 진행
        return None, 0.0
    if not recent:
        return None, 0.0

    candidates_text = "\n".join(
        f"- id={p['id']} | 제목: {p['title']} | 분야: {p['category']} | 연관 시리즈: {p['thread'] or '(없음)'}"
        for p in recent
    )
    client = OpenAI(api_key=openai_api_key)
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=500,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"방금 분석한 기사 제목: {data['title']}\n"
                    f"분야: {data['category']}\n"
                    f"요약: {' / '.join(data['summary'])}\n\n"
                    f"최근 저장된 기사 목록:\n{candidates_text}"
                ),
            },
        ],
        text={"format": {"type": "json_schema", "name": "thread_match", "strict": True, "schema": SCHEMA}},
    )
    if response.status != "completed":
        return None, 0.0
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, 0)
    result = json.loads(response.output_text)

    if not result["matched"] or not result["thread_name"]:
        return None, cost

    if result["matched_page_needs_backfill"] and result["matched_page_id"]:
        try:
            notion_client.set_thread_name(result["matched_page_id"], result["thread_name"], notion_token)
        except Exception:  # noqa: BLE001 - 예전 기사 소급 반영 실패해도 새 기사 저장은 계속 진행
            pass

    return result["thread_name"], cost
