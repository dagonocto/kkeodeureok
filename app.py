"""이 파일이 실제 웹페이지다.

Streamlit은 위에서부터 아래로 이 파일을 순서대로 실행해서 화면을 그린다.
`st.xxx()` 함수를 부르면 그 자리에 화면 요소(제목, 버튼, 업로드창 등)가 생긴다.
사용자가 버튼을 누르면 이 파일이 처음부터 다시 실행되는데, 그게 Streamlit의 동작 방식이다.

그런데 "다시 실행"되면 방금 만든 결과(result 변수 등)는 그냥 사라진다.
그래서 화면이 새로고침돼도 잃으면 안 되는 값은 `st.session_state`라는
"기억 상자"에 넣어둔다 — 이 상자는 재실행돼도 안 비워진다.
"""

import base64
import concurrent.futures
import json
import time
from urllib.parse import urlparse

import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI

from feedback import load_feedback_notes, save_feedback_note
from fetch_article import fetch_article_text
from notion_client import append_axis_block, create_notion_page, list_recent_pages
from prompts import FOLLOWUP_SCHEMA, FOLLOWUP_SYSTEM_PROMPT, RESPONSE_SCHEMA, SYSTEM_PROMPT
from usage_log import log_usage, total_cost

MODEL = "gpt-5.4-mini"

st.set_page_config(page_title="꺼드럭", page_icon="😎", layout="wide")
st.title("😎 꺼드럭")
st.write("깊게는 몰라도, 대화에서 꺼드럭댈 수 있게. 기사 원문(PDF, 이미지, 텍스트)을 올리면 배경지식과 해설을 만들어 Notion에 저장해줘요.")

# 화면 오른쪽 아래에 떠있는 "맨 위로" 버튼.
# st.markdown(unsafe_allow_html=True)은 보안상 onclick 속성과 <script> 태그를
# 걸러내버려서 못 쓴다 — 그래서 components.html로 진짜 실행되는 JS를 만든다.
# components.html은 iframe 안에서 도는데, "부모 문서"(window.parent.document)에
# 직접 버튼을 심어야 화면에 고정된 것처럼 보인다. 실제 스크롤되는 영역은
# window가 아니라 Streamlit의 section.stMain 이라서 그걸 대상으로 스크롤한다.
components.html(
    """
    <script>
    (function() {
        const doc = window.parent.document;
        if (doc.getElementById('scroll-top-btn')) return;  // 재실행마다 중복 생성 방지
        const style = doc.createElement('style');
        style.textContent = `
            #scroll-top-btn {
                position: fixed;
                bottom: 30px;
                right: 30px;
                z-index: 9999;
                background-color: #2F6FED;
                color: white;
                border: none;
                border-radius: 50%;
                width: 48px;
                height: 48px;
                font-size: 22px;
                cursor: pointer;
                box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            }
        `;
        doc.head.appendChild(style);
        const btn = doc.createElement('button');
        btn.id = 'scroll-top-btn';
        btn.title = '맨 위로';
        btn.innerText = '⬆';
        btn.onclick = function() {
            // Streamlit 버전에 따라 실제로 스크롤되는 요소가 다를 수 있어서,
            // 후보 여러 개에 한꺼번에 시도한다(스크롤 안 되는 요소에 걸어도 그냥 무시될 뿐 문제 없음).
            [doc.querySelector('.stMain'), doc.scrollingElement, doc.documentElement, doc.body]
                .filter(Boolean)
                .forEach(el => el.scrollTo({top: 0, behavior: 'smooth'}));
        };
        doc.body.appendChild(btn);
    })();
    </script>
    """,
    height=0,
)

# 기억 상자를 미리 준비해둔다. 처음 실행될 때만 초기값을 넣고,
# 재실행될 때는 이미 있는 값을 그대로 둔다("in" 조건으로 덮어쓰기 방지).
if "result" not in st.session_state:
    st.session_state.result = None
    st.session_state.page_url = None
    st.session_state.page_id = None
    st.session_state.last_cost = None
    st.session_state.document_block = None
    st.session_state.url_for_notion = None
if "dup_warning_url" not in st.session_state:
    # 같은 기사를 실수로 두 번 분석하면 돈도 두 번 나가고 Notion에 중복 페이지가 쌓인다.
    # 그래서 "분석 시작"을 누르면 먼저 이미 저장된 기사인지 확인하고, 맞으면 여기에
    # 그 URL을 잠깐 담아뒀다가 사용자가 "그래도 다시 분석하기"를 눌러야 진행한다.
    st.session_state.dup_warning_url = None
    st.session_state.dup_page_url = None

with st.expander("📚 최근 기록"):
    try:
        recent = list_recent_pages(st.secrets["NOTION_TOKEN"], st.secrets["NOTION_DATA_SOURCE_ID"], limit=10)
    except Exception as e:  # noqa: BLE001
        st.caption(f"기록을 불러오지 못했어요: {e}")
    else:
        if not recent:
            st.caption("아직 저장된 기록이 없어요.")
        for page in recent:
            st.markdown(f"- [{page['title']}]({page['url']}) · {page['category']}")

with st.container(border=True):
    # Streamlit은 위젯이 이미 그려진 뒤에는 그 위젯의 session_state를 직접 바꿀 수 없다.
    # 그래서 "지금 비워라" 표시만 미리 남겨두고, 위젯을 그리기 *전에* 그 표시를 보고
    # 비운 다음 표시를 내린다 — 다음 실행(rerun)에서만 적용되는 방식이다.
    if st.session_state.get("_clear_link_url"):
        st.session_state.link_url_input = ""
        st.session_state._clear_link_url = False

    # 링크 입력이 압도적으로 자주 쓰이는 방식이라, 이걸 기본(가장 먼저 보이는 입력창)으로 두고
    # 파일 업로드는 "링크가 안 될 때"를 위한 대안으로 접어(expander) 넣어둔다.
    link_url = st.text_input("기사 URL", key="link_url_input")
    st.caption("일부 사이트(로그인 필요, 크롤링 방지 등)는 본문을 못 가져올 수 있어요 — 그럴 땐 아래에서 파일로 올려주세요.")
    with st.expander("파일로 업로드하기 (링크가 안 될 때)"):
        uploaded_file = st.file_uploader(
            "기사 원문 파일",
            type=["pdf", "png", "jpg", "jpeg", "txt"],
        )
        source_url = st.text_input("기사 원문 URL (참고용, 있으면 입력)", key="source_url_ref")

# 파일을 올렸다면 "링크가 안 돼서 대안으로 쓴 것"으로 보고 파일을 우선한다.
input_mode = "파일 업로드" if uploaded_file is not None else "링크 입력"
ready = bool(uploaded_file is not None or link_url.strip())


def build_document_block(file) -> dict:
    """업로드된 파일을 OpenAI API가 이해하는 content block으로 변환한다."""
    raw_bytes = file.getvalue()
    b64 = base64.b64encode(raw_bytes).decode("utf-8")

    if file.type == "application/pdf":
        return {
            "type": "input_file",
            "filename": file.name,
            "file_data": f"data:application/pdf;base64,{b64}",
        }
    if file.type.startswith("image/"):
        return {
            "type": "input_image",
            "image_url": f"data:{file.type};base64,{b64}",
        }
    return {"type": "input_text", "text": raw_bytes.decode("utf-8")}


def build_system_prompt() -> str:
    """기본 지침 + 지금까지 쌓인 피드백을 합쳐서 최종 지침을 만든다."""
    notes = load_feedback_notes()
    if not notes:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n## 사용자가 남긴 과거 피드백 (반드시 참고해서 같은 실수를 반복하지 말 것)\n{notes}"


def analyze_article(document_block: dict, url: str) -> dict:
    """OpenAI API를 호출해서 기사 분석 결과(dict)를 받아온다."""
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    instruction = f"이 기사 원문을 분석해줘. 원문 URL: {url if url else '없음(파일로만 제공됨)'}"

    response = client.responses.create(
        model=MODEL,
        max_output_tokens=14000,
        input=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": [document_block, {"type": "input_text", "text": instruction}]},
        ],
        tools=[{"type": "web_search"}],
        text={
            "format": {
                "type": "json_schema",
                "name": "article_analysis",
                "strict": True,
                "schema": RESPONSE_SCHEMA,
            }
        },
    )

    if response.status != "completed":
        raise RuntimeError(f"모델 응답이 끝까지 완료되지 않았어요 (status={response.status})")

    # web_search 사용 요금은 response.usage에 안 잡히고, response.output 안에
    # "web_search_call" 항목이 몇 개 있는지로 직접 세야 한다.
    search_calls = sum(1 for item in response.output if item.type == "web_search_call")
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, search_calls)
    data = json.loads(response.output_text)

    # 모델이 언론사명을 못 찾으면 "확인 불가"를 돌려주는데, 그러면 Notion 페이지에
    # 출처가 아예 안 보인다. URL의 도메인이라도 있으면 그걸로 대신 채운다.
    if data.get("source_name") in (None, "", "확인 불가") and url:
        domain = urlparse(url).netloc.removeprefix("www.")
        if domain:
            data["source_name"] = domain

    return data, cost


# 축 패밀리마다 다른 아이콘 — notion_client.py의 FAMILY_ICONS와 짝을 맞춘다.
FAMILY_ICONS = {
    "용어 뽀개기": "📖",
    "원리 뽀개기": "⚙️",
    "인물관계도": "👥",
    "과거 썰": "🏛️",
    "싸움의 이유": "⚖️",
    "이거 오해였음": "🔍",
    "말 뒤에 숨은 뜻": "💬",
}


def render_result(data: dict, key_prefix: str = "") -> None:
    """분석 결과(dict)를 Notion에 쓰는 것과 똑같은 구조로 화면에 보여준다.

    key_prefix: 이 함수를 한 화면에 여러 번 부를 때(예: 여러 기사 한번에 보여주기)
    버튼 key가 서로 겹치지 않게 구분해주는 접두사.
    """
    st.markdown(f"#### {data['title']}")
    meta = f"{data['category']} · {data['source_name']}"
    if data["source_url"]:
        meta += f" · [원문 보기]({data['source_url']})"
    st.caption(meta)

    st.markdown("**📌 요약**")
    for point in data["summary"]:
        st.markdown(f"- {point}")

    if data["axes"]:
        st.markdown("**🔥 꺼드럭 포인트**")
        # 화면이 넓으면(데스크탑/노트북/태블릿) 좌우 2단으로 번갈아 배치하고,
        # 좁으면(휴대폰) 자동으로 위아래로 쌓인다 — st.columns()의 기본 반응형 동작.
        left, right = st.columns(2)
        for i, axis in enumerate(data["axes"]):
            col = left if i % 2 == 0 else right
            with col:
                with st.container(border=True):
                    icon = FAMILY_ICONS.get(axis["family"], "💡")
                    label = f"⚠️ {axis['title']}" if axis["sensitive"] else axis["title"]
                    st.markdown(f"**{icon} {label}**")
                    st.write(axis["explanation"])
                    if axis["confidence"] == "low":
                        st.caption("확실하지 않음 — 확인 필요")
                    # 추가 질문으로 붙은 축만 개별 출처(references)를 가진다 — 기존 축은
                    # 기사 전체 "더 파보고 싶으면" 목록으로 출처를 갈음하기 때문에 없을 수 있다.
                    for ref in axis.get("references", []):
                        title = f"[{ref['title']}]({ref['url']})" if ref["url"] else ref["title"]
                        st.caption(f"출처: {ref['source']} · {title}")
                    # 축 하나하나에 피드백을 남길 수 있게 한다 — 기사 전체가 아니라
                    # "이 축이 좋았는지/별로였는지"를 짚어줘야 다음 지침에 더 정확히 반영된다.
                    up_col, down_col = st.columns(2)
                    with up_col:
                        if st.button("👍", key=f"{key_prefix}axis_up_{i}"):
                            save_feedback_note(f"[{axis['family']}] '{axis['title']}' 축이 좋았음 — 이런 방향 유지")
                            st.toast("피드백 저장했어요!")
                    with down_col:
                        if st.button("👎", key=f"{key_prefix}axis_down_{i}"):
                            save_feedback_note(f"[{axis['family']}] '{axis['title']}' 축이 별로였음 — 개선 필요")
                            st.toast("피드백 저장했어요.")
    else:
        st.caption("배경지식 없이도 이해할 수 있는 기사로 판단돼, 별도 설명은 생략했어요.")

    st.markdown("**더 파보고 싶으면**")
    if not data["references"]:
        st.caption("없음")
    for ref in data["references"]:
        title = f"[{ref['title']}]({ref['url']})" if ref["url"] else ref["title"]
        st.markdown(f"- {ref['source']} · {title}")


def save_to_notion(result: dict):
    """분석 결과를 Notion에 저장하고, 성공하면 링크를 기억 상자에 넣는다.

    분석(돈이 드는 부분)과 Notion 저장(무료)을 분리해두는 이유:
    분석은 이미 성공했는데 Notion 저장만 실패하면, 결과를 잃어버려서
    돈을 또 내고 분석을 다시 할 필요가 없도록 하기 위해서다.
    """
    try:
        page_url, page_id = create_notion_page(
            result,
            notion_token=st.secrets["NOTION_TOKEN"],
            data_source_id=st.secrets["NOTION_DATA_SOURCE_ID"],
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Notion 저장에 실패했어요: {e} — 분석 결과는 안 사라졌으니 아래에서 다시 시도할 수 있어요.")
    else:
        st.session_state.page_url = page_url
        st.session_state.page_id = page_id


def answer_followup(document_block: dict, url: str, question: str) -> tuple[dict, float]:
    """이미 분석한 기사 원문을 재사용해서, 추가 질문에 답하는 축 하나만 새로 만든다."""
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    instruction = f"추가 질문: {question}\n원문 URL: {url if url else '없음(파일로만 제공됨)'}"

    response = client.responses.create(
        model=MODEL,
        max_output_tokens=3500,
        input=[
            {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
            {"role": "user", "content": [document_block, {"type": "input_text", "text": instruction}]},
        ],
        tools=[{"type": "web_search"}],
        text={
            "format": {
                "type": "json_schema",
                "name": "followup_axis",
                "strict": True,
                "schema": FOLLOWUP_SCHEMA,
            }
        },
    )

    if response.status != "completed":
        raise RuntimeError(f"모델 응답이 끝까지 완료되지 않았어요 (status={response.status})")

    search_calls = sum(1 for item in response.output if item.type == "web_search_call")
    cost = log_usage(response.usage.input_tokens, response.usage.output_tokens, search_calls)
    return json.loads(response.output_text), cost


def find_duplicate_page_url(target_url: str) -> str | None:
    """이미 저장된 기사 중 원문 URL이 같은 게 있으면 그 Notion 페이지 URL을 돌려준다."""
    if not target_url:
        return None
    target_url = target_url.strip().rstrip("/")
    try:
        recent = list_recent_pages(st.secrets["NOTION_TOKEN"], st.secrets["NOTION_DATA_SOURCE_ID"], limit=30)
    except Exception:  # noqa: BLE001 - 중복 체크 실패는 조용히 넘어가고 정상 분석을 막지 않는다
        return None
    for page in recent:
        if page.get("source_url") and page["source_url"].strip().rstrip("/") == target_url:
            return page["url"]
    return None


def analyze_with_progress(document_block: dict, url: str):
    """analyze_article을 실행하는 동안 진행률 표시줄을 보여준다.

    실제 진행률은 알 수 없다(API 호출 한 번이 끝나야 결과가 통째로 오기 때문에
    중간 단계를 들여다볼 수 없음). 그래서 analyze_article을 별도 스레드에서 돌리고,
    메인 스레드는 그 사이에 표시줄을 적당한 속도로 채워서 "기다리고 있다"는 걸
    체감상 더 빠르고 확실하게 보여준다 — 95%까지 채우고, 끝나면 100%로 마무리.
    """
    progress = st.progress(0, text="기사를 읽는 중...")
    stages = [(15, "기사를 읽는 중..."), (45, "배경을 검색하는 중..."), (80, "설명을 작성하는 중...")]
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(analyze_article, document_block, url)
        pct, stage_idx = 0, 0
        while not future.done():
            pct = min(pct + 2, 95)
            if stage_idx < len(stages) - 1 and pct >= stages[stage_idx][0]:
                stage_idx += 1
            progress.progress(pct, text=stages[stage_idx][1])
            time.sleep(0.25)
        progress.progress(100, text="완료!")
        result = future.result()  # analyze_article 안에서 에러가 났으면 여기서 다시 던져진다
    progress.empty()
    return result


def perform_analysis():
    """실제 분석 1건을 실행한다. 정상 경로와 "그래도 다시 분석하기" 경로가 함께 쓴다."""
    try:
        if input_mode == "링크 입력":
            article_text = fetch_article_text(link_url)
            document_block = {"type": "input_text", "text": article_text}
            url_for_notion = link_url
        else:
            document_block = build_document_block(uploaded_file)
            url_for_notion = source_url

        result, cost = analyze_with_progress(document_block, url_for_notion)
    except Exception as e:  # noqa: BLE001 - 화면에 에러를 그대로 보여주기 위해 넓게 잡음
        st.error(f"분석 중 문제가 발생했어요: {e}")
    else:
        # 기억 상자에 저장 — 이후 재실행(피드백 저장 버튼 등)돼도 안 사라짐.
        # document_block/url_for_notion도 같이 저장해두는 이유: "추가 질문" 기능에서
        # 원문을 다시 업로드/입력받지 않고 그대로 재사용하기 위해서다.
        st.session_state.result = result
        st.session_state.last_cost = cost
        st.session_state.page_url = None
        st.session_state.page_id = None
        st.session_state.document_block = document_block
        st.session_state.url_for_notion = url_for_notion
        st.session_state.dup_warning_url = None
        save_to_notion(result)
        if input_mode == "링크 입력":
            # 다음 기사를 바로 이어서 검색할 수 있도록 입력창을 비운다.
            # (위젯이 이미 그려진 뒤라 여기서 직접 값을 바꿀 수 없어서, 표시만
            # 남기고 다음 실행 시작 부분에서 실제로 비운다)
            st.session_state._clear_link_url = True
        st.rerun()


if st.button("분석 시작"):
    # 버튼을 처음부터 막아두지(disabled) 않는 이유: text_input은 엔터를 치거나 다른 곳을
    # 눌러야 값이 "확정"되는데, 그걸 기준으로 버튼을 비활성화해두면 확정되기 전에는
    # 버튼 자체가 안 눌려서 결국 엔터를 강제로 쳐야 하는 상황이 된다. 그래서 버튼은
    # 항상 눌리게 두고, 눌렀을 때 값이 비어있는지를 확인하는 방식으로 바꿨다.
    if not ready:
        st.warning("파일을 올리거나 링크를 입력해주세요.")
    else:
        target_url = link_url.strip() if input_mode == "링크 입력" else source_url.strip()
        dup_page_url = find_duplicate_page_url(target_url)
        if dup_page_url:
            st.session_state.dup_warning_url = target_url
            st.session_state.dup_page_url = dup_page_url
        else:
            perform_analysis()

if st.session_state.dup_warning_url:
    st.warning(
        f"이미 분석한 적 있는 기사 같아요 — [기존 결과 보기]({st.session_state.dup_page_url}). "
        "그래도 새로 분석하려면 아래 버튼을 눌러주세요."
    )
    if st.button("그래도 다시 분석하기"):
        perform_analysis()

if st.session_state.result:
    st.write("")
    with st.container(border=True):
        if st.session_state.page_url:
            st.success("완료! Notion에 저장됐어요.")
            st.markdown(f"[Notion에서 열기]({st.session_state.page_url})")
        else:
            st.warning("분석은 끝났지만 아직 Notion에 저장되지 않았어요.")
            if st.button("Notion에 다시 저장"):
                save_to_notion(st.session_state.result)
        st.caption(f"이번 호출 비용: 약 ${st.session_state.last_cost:.4f} · 누적: 약 ${total_cost():.4f}")
        st.divider()
        render_result(st.session_state.result)

        st.divider()
        st.markdown("**🙋 더 궁금한 점 있어요?**")
        if st.session_state.get("_clear_followup"):
            st.session_state.followup_input = ""
            st.session_state._clear_followup = False
        followup_q = st.text_input(
            "추가 질문", key="followup_input", label_visibility="collapsed",
            placeholder="예: OO는 왜 그렇게 말했을까?",
        )
        if st.button("추가로 물어보기"):
            if not followup_q.strip():
                st.warning("질문을 입력해주세요.")
            else:
                with st.spinner("답을 찾는 중이에요..."):
                    try:
                        new_axis, cost = answer_followup(
                            st.session_state.document_block, st.session_state.url_for_notion, followup_q.strip()
                        )
                    except Exception as e:  # noqa: BLE001
                        st.error(f"추가 질문 처리 중 문제가 발생했어요: {e}")
                    else:
                        st.session_state.result["axes"].append(new_axis)
                        st.session_state.last_cost += cost
                        if st.session_state.page_id:
                            try:
                                append_axis_block(st.session_state.page_id, new_axis, st.secrets["NOTION_TOKEN"])
                            except Exception as e:  # noqa: BLE001
                                st.warning(f"화면엔 추가됐지만 Notion에는 못 붙였어요: {e}")
                        st.session_state._clear_followup = True  # 다음 질문을 편하게 이어 물어볼 수 있도록 비운다
                        st.rerun()  # 방금 추가된 축이 위쪽 결과 화면에 바로 보이도록 다시 그린다

    st.write("")
    with st.container(border=True):
        st.subheader("📝 피드백")
        st.caption("아쉬운 점을 적어두면 다음 실행부터 지침에 반영돼요.")
        feedback_text = st.text_area("피드백", label_visibility="collapsed")
        if st.button("피드백 저장") and feedback_text.strip():
            save_feedback_note(feedback_text.strip())
            st.success("저장했어요. 다음 실행부터 반영돼요.")

st.write("")

# 여러 기사를 몰아서 읽을 때, 하나씩 넣고 기다리는 걸 반복하지 않아도 되게 만든 기능.
# 위의 단일 기사 흐름과는 별개로 독립적으로 동작한다(결과도 따로 st.session_state.batch_results에 저장).
if "batch_results" not in st.session_state:
    st.session_state.batch_results = []

with st.expander("🗞 여러 기사 한번에 분석하기"):
    batch_text = st.text_area("기사 URL 목록 (한 줄에 하나씩)", height=120, key="batch_urls")
    if st.button("모두 분석 시작"):
        urls = [line.strip() for line in batch_text.splitlines() if line.strip()]
        if not urls:
            st.warning("URL을 한 줄에 하나씩 입력해주세요.")
        else:
            st.session_state.batch_results = []
            overall = st.progress(0, text=f"0/{len(urls)} 처리 중...")
            for i, u in enumerate(urls):
                overall.progress(i / len(urls), text=f"{i}/{len(urls)} 처리 중... ({u})")
                entry = {"url": u, "result": None, "page_url": None, "error": None}
                try:
                    text = fetch_article_text(u)
                    doc_block = {"type": "input_text", "text": text}
                    result, cost = analyze_article(doc_block, u)
                    page_url, _ = create_notion_page(
                        result,
                        notion_token=st.secrets["NOTION_TOKEN"],
                        data_source_id=st.secrets["NOTION_DATA_SOURCE_ID"],
                    )
                    entry["result"] = result
                    entry["page_url"] = page_url
                except Exception as e:  # noqa: BLE001 - 하나 실패해도 나머지는 계속 진행
                    entry["error"] = str(e)
                st.session_state.batch_results.append(entry)
            overall.progress(1.0, text=f"{len(urls)}/{len(urls)} 완료!")

    if st.session_state.batch_results:
        ok = sum(1 for e in st.session_state.batch_results if e["result"])
        st.caption(f"총 {len(st.session_state.batch_results)}건 중 {ok}건 성공")
        for idx, entry in enumerate(st.session_state.batch_results):
            if entry["result"]:
                with st.expander(f"✅ {entry['result']['title']}"):
                    st.markdown(f"[Notion에서 열기]({entry['page_url']})")
                    render_result(entry["result"], key_prefix=f"batch_{idx}_")
            else:
                st.error(f"❌ {entry['url']} — {entry['error']}")
