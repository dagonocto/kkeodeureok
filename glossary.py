"""꺼드럭이 축적하는 용어사전 — Notion 데이터베이스에 저장한다.

로컬 파일(csv/json)로 만들지 않은 이유: Streamlit Cloud는 앱이 재시작/재배포될 때마다
로컬 파일이 초기화될 수 있어서, "쌓인다"는 목적 자체가 깨진다. Notion은 이미 이 앱이
쓰고 있는 영구 저장소라 여기에 얹었다 — 덤으로 사용자가 Notion에서 직접 정의를 열어보고
고칠 수도 있다.

흐름: "용어 뽀개기" 축을 쓸 때, 먼저 lookup()으로 이미 정의된 용어인지 확인한다.
있으면 검색 없이 재사용(비용 절감 + 일관성), 없으면 평소처럼 Perplexity로 검색한 뒤
save()로 새로 추가해서 다음에는 재사용되게 한다.
"""

import requests

NOTION_VERSION = "2026-03-11"


def _headers(notion_token: str) -> dict:
    return {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def lookup(term: str, notion_token: str, data_source_id: str) -> str | None:
    """용어사전에서 term과 정확히 일치하는 "용어"를 찾아 "정의"를 돌려준다. 없으면 None.

    조회 실패(네트워크 오류 등)는 조용히 None을 돌려준다 — 사전 조회가 안 된다고
    분석 전체가 멈추면 안 되고, 그냥 검색해서 새로 쓰는 경로로 넘어가면 되기 때문이다.
    """
    try:
        body = {
            "filter": {"property": "용어", "title": {"equals": term}},
            "page_size": 1,
        }
        response = requests.post(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
            headers=_headers(notion_token),
            json=body,
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()["results"]
    except Exception:  # noqa: BLE001
        return None

    if not results:
        return None
    rich_text = results[0]["properties"].get("정의", {}).get("rich_text", [])
    return "".join(t["plain_text"] for t in rich_text) or None


def delete(term: str, notion_token: str, data_source_id: str) -> None:
    """용어사전에서 term과 일치하는 항목을 지운다(휴지통 이동).

    사용자가 화면에서 "이 용어 설명 별로였음"을 눌렀을 때 호출한다 — 다음 번엔 이 용어가
    사전에 없으니 새로 검색해서 다시 쓰게 된다. 조회/삭제 둘 다 실패해도 조용히 넘어간다 —
    사전 삭제가 안 됐다고 피드백 저장 자체가 실패해 보이면 안 되기 때문이다.
    """
    try:
        body = {"filter": {"property": "용어", "title": {"equals": term}}, "page_size": 1}
        response = requests.post(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
            headers=_headers(notion_token),
            json=body,
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()["results"]
        if not results:
            return
        page_id = results[0]["id"]
        requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_headers(notion_token),
            json={"archived": True},
            timeout=15,
        ).raise_for_status()
    except Exception:  # noqa: BLE001
        pass


def save(term: str, definition: str, notion_token: str, data_source_id: str) -> None:
    """용어사전에 새 용어를 추가한다. 실패해도 예외를 삼킨다 — 사전 저장은 부가 기능이라
    이것 때문에 기사 분석 자체가 실패해 보이면 안 된다.
    """
    body = {
        "parent": {"type": "data_source_id", "data_source_id": data_source_id},
        "properties": {
            "용어": {"title": [{"type": "text", "text": {"content": term}}]},
            "정의": {"rich_text": [{"type": "text", "text": {"content": definition[:2000]}}]},
        },
    }
    try:
        response = requests.post(
            "https://api.notion.com/v1/pages", headers=_headers(notion_token), json=body, timeout=15
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        pass
