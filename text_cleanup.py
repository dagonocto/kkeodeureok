"""모델 응답 텍스트에 남는 잡음을 후처리로 지우는 작은 유틸리티.

web_search 인용을 구조화된 JSON 출력과 같이 쓰면, 가끔 텍스트 끝에 `}],` 같은 JSON
구조 조각이 그대로 섞여 나온다 — 프롬프트로 "하지 마라"고 지시해도 완전히는 안 없어지는,
API 레벨에서 새는 현상으로 보인다. app.py와 regression_test.py 양쪽에서 똑같이 써야 해서
따로 뺐다.
"""

import re

_TRAILING_ARTIFACT_RE = re.compile(r"(?:[\}\]\,]{2,}|,)\s*$")

# Perplexity 응답(research_findings의 answer)에는 "...이겼습니다.[web:151][web:153]"처럼
# 자체 인용 마커가 붙어 있다. 작성 단계 프롬프트에서 "그대로 베끼지 말라"고 지시해도
# 가끔 새어 나와서, 다른 아티팩트 제거와 같은 자리에서 정규식으로 한 번 더 걸러낸다.
# 마커 안 식별자는 항상 숫자인 게 아니라 "[web:rtFMuuGnEXoENoCaD0RXUkBD]"처럼 영숫자
# 해시로 나오는 경우도 있다. 다만 "web/cite/ref:" 접두어 없이 영숫자 전체를 다 허용하면
# 본문에 정말 쓰인 괄호 표기(예: 약어)까지 지워버릴 수 있어서, 접두어가 있을 때만 영숫자
# 해시를 허용하고, 접두어가 없는 경우엔 기존처럼 순수 숫자([151] 같은 맨 번호)만 잡는다.
_CITATION_MARKER_RE = re.compile(r"\[(?:(?:web|cite|ref):[A-Za-z0-9_-]+|\d+)\]", re.IGNORECASE)


def strip_trailing_artifacts(value):
    """문자열 끝에 남은 `}],` 조각과, 본문에 새어 들어온 `[web:3]` 같은 인용 마커를 지운다.
    dict/list는 재귀적으로 처리한다.
    """
    if isinstance(value, str):
        cleaned = _CITATION_MARKER_RE.sub("", value)
        return _TRAILING_ARTIFACT_RE.sub("", cleaned).rstrip()
    if isinstance(value, list):
        return [strip_trailing_artifacts(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_trailing_artifacts(v) for k, v in value.items()}
    return value
