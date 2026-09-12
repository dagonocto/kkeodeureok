"""prompts.py 로딩을 앞단에서 보장해주는 부트스트랩.

prompts.py(실제 AI에게 던지는 지시문 — 이 프로젝트의 핵심 프롬프트 엔지니어링)는
이 저장소에 없다. 팀원과 협업하려고 저장소를 공유하되, 프롬프트 자체는 비공개로
유지하려고 별도 private 저장소(kkeodeureok-core)로 옮겼기 때문이다(2026-09-12).

환경별로 이렇게 동작한다:
- 소유자 로컬 환경: 이 폴더 안에 prompts.py 실물 파일이 그대로 남아있다(git으로
  추적만 안 할 뿐 파일 자체를 지운 게 아니다) — 그래서 그냥 평소처럼 정상 import된다.
- 팀원 로컬 환경: 그 파일이 없으니 최초 import가 실패한다 — 이땐 기사 분석/추가
  질문 기능만 조용히 비활성화하고, 나머지(카드뉴스·PDF·최근기록 뷰어 등)는 정상
  동작해야 한다. cardnews/report_pdf를 optional 취급하는 것과 같은 원칙이다.
- 배포 서버(Streamlit Cloud): 마찬가지로 파일이 없다. 근데 여긴 실제 서비스가
  돌아가야 하니, Streamlit Secrets의 PROMPTS_PY_SOURCE(prompts.py 전체 내용을
  문자열로 저장해둔 것)를 읽어서 그 자리에서 모듈을 만들어 등록한다.
  ⚠️ prompts.py를 고칠 때마다 이 Secrets 값도 최신 내용으로 같이 업데이트해야
  배포에 반영된다 — 안 하면 배포된 서버는 예전 프롬프트로 계속 돈다.

app.py는 `import analysis_pipeline`보다 먼저 이 모듈의 ensure_prompts_available()을
호출해야 한다 — analysis_pipeline.py도 내부에서 `from prompts import ...`를 하므로,
그 시점에 이미 sys.modules["prompts"]가 채워져 있어야 한다.
"""

import base64
import sys
import types


def ensure_prompts_available() -> bool:
    """prompts 모듈을 쓸 수 있게 준비한다. 성공하면 True, 실패하면 False."""
    if "prompts" in sys.modules:
        return True

    try:
        import prompts  # noqa: F401 - 로컬에 실물 파일이 있으면 이걸로 끝, 아래로 안 내려감
        return True
    except ImportError:
        pass

    try:
        import streamlit as st
    except ImportError:
        return False

    try:
        encoded = st.secrets.get("PROMPTS_PY_SOURCE_B64")
    except Exception:  # noqa: BLE001 - secrets.toml 자체가 없거나 접근 실패해도 조용히 실패 처리
        encoded = None
    if not encoded:
        return False

    # prompts.py 안에는 """로 감싼 멀티라인 문자열이 잔뜩 있어서, TOML에 원문 그대로
    # 넣으면 그 """가 TOML 문자열을 중간에 끊어버린다. 그래서 base64로 인코딩해서
    # 저장해둔다 — base64 결과엔 따옴표·줄바꿈이 없어서 TOML 문자열로 안전하다.
    try:
        source = base64.b64decode(encoded).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False

    module = types.ModuleType("prompts")
    try:
        exec(compile(source, "<PROMPTS_PY_SOURCE_B64 secret>", "exec"), module.__dict__)
    except Exception:  # noqa: BLE001 - secret 내용이 깨져 있어도 앱 전체를 죽이지 않는다
        return False

    sys.modules["prompts"] = module
    return True
