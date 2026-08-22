"""모델 응답 텍스트에 남는 잡음을 후처리로 지우는 작은 유틸리티.

web_search 인용을 구조화된 JSON 출력과 같이 쓰면, 가끔 텍스트 끝에 `}],` 같은 JSON
구조 조각이 그대로 섞여 나온다 — 프롬프트로 "하지 마라"고 지시해도 완전히는 안 없어지는,
API 레벨에서 새는 현상으로 보인다. app.py와 regression_test.py 양쪽에서 똑같이 써야 해서
따로 뺐다.
"""

import re

_TRAILING_ARTIFACT_RE = re.compile(r"[\}\]\,]{2,}\s*$")


def strip_trailing_artifacts(value):
    """문자열 끝에 남은 `}],` 같은 조각을 지운다. dict/list는 재귀적으로 처리한다."""
    if isinstance(value, str):
        return _TRAILING_ARTIFACT_RE.sub("", value).rstrip()
    if isinstance(value, list):
        return [strip_trailing_artifacts(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_trailing_artifacts(v) for k, v in value.items()}
    return value
