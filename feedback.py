"""사용자 피드백을 파일에 쌓아두는 역할만 하는 파일.

모델 자체가 피드백을 받아서 재학습되는 게 아니다 — 그런 기능은 없다.
대신 "이런 실수를 하지 마라"는 문장을 lessons.md에 계속 추가하고,
그 내용을 다음 호출 때마다 지침(system prompt)에 같이 끼워 넣는 방식으로
결과물이 점점 나아지게 만든다. 즉, 모델이 아니라 "지침"이 성장하는 것.

주의: lessons.md는 계속 append만 되므로 무한정 커지면 매 호출마다 토큰(비용)이
조금씩 늘어난다. 그래서 2주 정도 쓴 뒤에는 이 파일을 검토해서 중복되거나
당연해진 규칙은 prompts.py의 SYSTEM_PROMPT로 영구 흡수시키고, 이 파일은
다시 비우는 "정리(consolidation)" 과정을 한 번 거칠 것.
"""

from datetime import date

LESSONS_FILE = "lessons.md"


def load_feedback_notes() -> str:
    """지금까지 쌓인 피드백을 읽어온다. 파일이 아직 없으면 빈 문자열."""
    try:
        with open(LESSONS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_feedback_note(note: str) -> None:
    """새 피드백 한 줄을 파일 맨 끝에 추가한다 (기존 내용은 지워지지 않고 계속 쌓인다)."""
    with open(LESSONS_FILE, "a", encoding="utf-8") as f:
        f.write(f"- ({date.today().isoformat()}) {note}\n")
