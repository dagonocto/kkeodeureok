"""Notion API에 페이지 하나를 만드는 역할만 담당하는 파일.

Claude가 만들어준 결과(JSON)를 받아서, 그걸 Notion이 이해하는 형식으로
"번역"해서 실제로 페이지를 생성한다. app.py는 이 파일의 함수만 호출하면 되고,
Notion API의 세부 형식은 몰라도 된다 — 이게 함수로 나누는(모듈화) 이유다.
"""

import re
from datetime import date

import requests

NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2026-03-11"


def _data_source_query_url(data_source_id: str) -> str:
    return f"https://api.notion.com/v1/data_sources/{data_source_id}/query"


def _block_children_url(block_id: str) -> str:
    return f"https://api.notion.com/v1/blocks/{block_id}/children"

# 축(axis)의 패밀리마다 다른 아이콘을 붙여서 콜아웃을 구분한다.
FAMILY_ICONS = {
    "용어 뽀개기": "📖",
    "원리 뽀개기": "⚙️",
    "인물관계도": "👥",
    "과거 썰": "🏛️",
    "싸움의 이유": "⚖️",
    "이거 오해였음": "🔍",
    "말 뒤에 숨은 뜻": "💬",
}


def _text(content: str, bold: bool = False) -> dict:
    """Notion의 '리치 텍스트' 조각 하나를 만드는 작은 헬퍼.

    Notion API는 문자열을 그냥 "content" 로 받지 않고,
    {"type": "text", "text": {"content": "..."}} 처럼 감싸서 보내야 한다.
    굵게 표시하고 싶으면 "**텍스트**"처럼 별표를 직접 쓰는 게 아니라
    annotations.bold를 True로 줘야 한다 — Notion은 마크다운을 자동 해석하지 않는다.
    """
    obj = {"type": "text", "text": {"content": content}}
    if bold:
        obj["annotations"] = {"bold": True}
    return obj


def _link(content: str, url: str) -> dict:
    """클릭하면 이동되는 링크가 걸린 리치 텍스트 조각을 만든다."""
    return {"type": "text", "text": {"content": content, "link": {"url": url}}}


def _callout(icon_emoji: str, label: str, body_runs: list[dict]) -> dict:
    """콜아웃(강조 박스) 블록 하나를 만든다. label은 굵게, body_runs는 이미 만들어진 리치 텍스트 조각들."""
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": icon_emoji},
            "rich_text": [_text(f"{label}\n", bold=True), *body_runs],
        },
    }


_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")


def _explanation_to_rich_text(explanation: str) -> list[dict]:
    """explanation 문자열(작성 단계 프롬프트가 넣는 마크다운 — 소제목은 `**굵게**` 한 줄,
    나열형 사실은 `- ` 불릿)을 Notion 리치 텍스트 조각으로 바꾼다. Notion API는 마크다운을
    자동 해석하지 않으므로, 굵게 표시할 부분은 우리가 직접 annotations.bold를 넣어줘야 한다.
    """
    runs = []
    lines = explanation.split("\n")
    for i, line in enumerate(lines):
        suffix = "\n" if i < len(lines) - 1 else ""
        bold_match = _BOLD_LINE_RE.match(line.strip())
        if bold_match:
            runs.append(_text(bold_match.group(1) + suffix, bold=True))
        elif line.startswith("- "):
            runs.append(_text("• " + line[2:] + suffix))
        else:
            runs.append(_text(line + suffix))
    return runs


def _heading2(content: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [_text(content)]},
    }


def _bullet(content: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_text(content)]},
    }


def _reference_bullet(source: str, title: str, url: str | None) -> dict:
    """참고자료 한 줄을 만든다. url이 있으면 title 부분이 클릭 가능한 링크가 된다."""
    title_run = _link(title, url) if url else _text(title)
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_text(f"{source} · "), title_run]},
    }


def _axis_callout(axis: dict) -> dict:
    """축 하나를 콜아웃 블록으로 만든다. 민감/저확신 표시를 라벨·본문에 덧붙인다.

    talk_line("말할 거리")이 있으면 본문 맨 끝에 💬로 구분해서 붙인다 — 이게 이 서비스의
    핵심(대화에서 아는 척할 수 있는 한 줄)이라 설명과 시각적으로 구분되게 둔다.
    """
    icon = FAMILY_ICONS.get(axis["family"], "💡")
    label = f"⚠️ {axis['title']}" if axis["sensitive"] else axis["title"]
    body_runs = _explanation_to_rich_text(axis["explanation"])
    if axis["confidence"] == "low":
        body_runs.append(_text("\n(확실하지 않음 — 확인 필요)"))
    talk_line = axis.get("talk_line")
    if talk_line:
        body_runs.append(_text(f"\n\n💬 {talk_line}"))
    return _callout(icon, label, body_runs)


def build_children_blocks(data: dict) -> list[dict]:
    """Claude 응답(dict)을 받아서 Notion 페이지 본문 블록 리스트를 만든다."""
    blocks = [_heading2("📌 요약")]
    blocks += [_bullet(point) for point in data["summary"]]

    if data["axes"]:
        blocks.append(_heading2("🔥 꺼드럭 포인트"))
        for axis in data["axes"]:
            blocks.append(_axis_callout(axis))

    blocks.append(_heading2("출처"))
    blocks.append(_reference_bullet(data["source_name"], "기사 원문", data["source_url"]))

    blocks.append(_heading2("더 파보고 싶으면"))
    for ref in data["references"]:
        blocks.append(_reference_bullet(ref["source"], ref["title"], ref["url"]))

    return blocks


def build_properties(data: dict) -> dict:
    """Claude 응답(dict)을 Notion 데이터베이스의 속성(properties) 형식으로 변환."""
    properties = {
        "제목": {"title": [_text(data["title"])]},
        "날짜": {"date": {"start": date.today().isoformat()}},
        "분야": {"select": {"name": data["category"]}},
        "출처명": {"rich_text": [_text(data["source_name"])]},
        "행정학 키워드": {"multi_select": [{"name": kw} for kw in data["keywords"]]},
        "주요 뉴스": {"checkbox": False},
    }
    if data["source_url"]:
        properties["출처 URL"] = {"url": data["source_url"]}
    if data.get("thread_name"):
        properties["연관 시리즈"] = {"rich_text": [_text(data["thread_name"])]}
    return properties


def create_notion_page(data: dict, notion_token: str, data_source_id: str) -> tuple[str, str]:
    """실제로 Notion에 페이지를 생성하고, (페이지 URL, 페이지 ID)를 돌려준다.

    페이지 ID는 나중에 "추가 질문" 기능에서 같은 페이지에 내용을 덧붙일 때 필요하다.
    """
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"type": "data_source_id", "data_source_id": data_source_id},
        "properties": build_properties(data),
        "children": build_children_blocks(data),
    }

    response = requests.post(NOTION_API_URL, headers=headers, json=body, timeout=30)
    response.raise_for_status()  # 요청이 실패(4xx/5xx)하면 여기서 예외를 던져서 바로 알 수 있게 함
    page = response.json()
    return page["url"], page["id"]


def append_axis_block(page_id: str, axis: dict, notion_token: str) -> None:
    """이미 만들어진 페이지 맨 끝에 축 하나를 "추가 질문" 형태로 덧붙인다."""
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    children = [_heading2("🙋 추가 질문"), _axis_callout(axis)]
    for ref in axis.get("references", []):
        children.append(_reference_bullet(ref["source"], ref["title"], ref["url"]))
    body = {"children": children}
    response = requests.patch(_block_children_url(page_id), headers=headers, json=body, timeout=30)
    response.raise_for_status()


def list_recent_pages(notion_token: str, data_source_id: str, limit: int = 10) -> list[dict]:
    """최근 저장한 기사 목록을 가져온다. 각 항목은 id/title/category/url/source_url/date/thread를 담은 dict.

    source_url은 "이미 분석한 기사인지" 중복 체크에 쓰인다 — app.py에서 새로 입력된
    기사 URL이 이 목록의 source_url과 겹치면 재분석 전에 사용자에게 물어본다.
    thread는 "연관 시리즈" 값(story_thread.py가 기사끼리 연결할 때 쓰는 이름) — 아직
    안 붙어 있으면 None이다.
    """
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "sorts": [{"property": "날짜", "direction": "descending"}],
        "page_size": limit,
    }
    response = requests.post(_data_source_query_url(data_source_id), headers=headers, json=body, timeout=30)
    response.raise_for_status()

    pages = []
    for page in response.json()["results"]:
        props = page["properties"]
        title_runs = props.get("제목", {}).get("title", [])
        title = title_runs[0]["plain_text"] if title_runs else "(제목 없음)"
        category = (props.get("분야", {}).get("select") or {}).get("name", "")
        source_url = (props.get("출처 URL") or {}).get("url")
        thread_runs = (props.get("연관 시리즈") or {}).get("rich_text", [])
        thread = thread_runs[0]["plain_text"] if thread_runs else None
        date_info = (props.get("날짜") or {}).get("date") or {}
        pages.append({
            "id": page["id"],
            "title": title,
            "category": category,
            "url": page["url"],
            "source_url": source_url,
            "date": date_info.get("start"),
            "thread": thread or None,
        })
    return pages


def set_thread_name(page_id: str, thread_name: str, notion_token: str) -> None:
    """이미 저장된 페이지에 "연관 시리즈" 이름을 소급해서 붙인다(속성만 갱신, 본문은 그대로).

    story_thread.py가 "이 새 기사는 예전에 저장된 저 기사의 후속이다"라고 판단했는데
    그 예전 기사에 아직 시리즈 이름이 없을 때, 두 기사를 같은 이름으로 묶기 위해 쓴다.
    """
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {"properties": {"연관 시리즈": {"rich_text": [_text(thread_name)]}}}
    response = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=headers, json=body, timeout=30)
    response.raise_for_status()
