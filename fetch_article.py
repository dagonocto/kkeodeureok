"""기사 URL을 받아서 본문 텍스트만 뽑아내는 역할만 하는 파일.

웹페이지 HTML에는 광고, 메뉴, 관련기사 링크 같은 게 섞여 있어서
그냥 통째로 가져오면 안 된다. `trafilatura`라는 라이브러리가
"이 페이지에서 진짜 기사 본문이 어디인지"를 자동으로 판단해서 뽑아준다.

주의: 로그인이 필요한 기사, 자바스크립트로만 렌더링되는 사이트,
크롤링을 막아둔 사이트는 실패할 수 있다. 그런 경우엔 파일 업로드 방식을 쓰면 된다.

다운로드는 trafilatura.fetch_url 대신 requests로 직접 받는다 — trafilatura가 내부에서
쓰는 urllib3 풀은 TLS 트래픽을 가로채 재서명하는 프록시(클라우드 루틴 실행 환경 등) 아래에서
연결 자체가 조용히 실패하는 경우가 있었다(로컬 환경에서는 재현되지 않음). requests는 시스템
프록시·인증서 설정을 그대로 따라가므로 두 환경 모두에서 안정적으로 동작한다. 받아온 HTML을
trafilatura.extract()에 넘겨 본문만 뽑아내는 부분은 그대로 유지한다.
"""

import requests
import trafilatura

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_article(url: str) -> tuple[str, str | None]:
    """URL에서 (기사 본문 텍스트, 실제 발행일 "YYYY-MM-DD" 또는 None)을 함께 추출한다.

    발행일은 trafilatura가 HTML 안의 메타 태그·날짜 표기를 분석해서 뽑아준다(htmldate 기반).
    사이트에 따라 못 찾을 수도 있는데, 그럴 땐 None을 돌려주고 호출하는 쪽이 오늘 날짜 등으로
    대체하면 된다 — 발행일을 못 찾았다고 분석 자체를 실패시키지 않는다.
    """
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
        downloaded = response.text
    except requests.exceptions.RequestException:
        downloaded = None

    if not downloaded:
        raise RuntimeError("이 URL에 접속할 수 없었어요. 주소가 맞는지 확인해주세요.")

    text = trafilatura.extract(downloaded)
    if not text or len(text) < 100:
        raise RuntimeError(
            "이 페이지에서 본문을 뽑아내지 못했어요. "
            "로그인이 필요한 기사이거나 크롤링이 막혀있을 수 있어요 — 파일 업로드로 시도해주세요."
        )

    published_date = None
    try:
        meta = trafilatura.extract_metadata(downloaded, default_url=url)
        if meta and meta.date:
            published_date = meta.date
    except Exception:  # noqa: BLE001 - 발행일 추출 실패는 본문 추출 성공 자체를 막지 않는다
        pass

    return text, published_date


def fetch_article_text(url: str) -> str:
    """하위 호환용 — 본문 텍스트만 필요할 때(회귀 테스트 등, 발행일이 필요 없는 경우)."""
    text, _ = fetch_article(url)
    return text
