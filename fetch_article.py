"""기사 URL을 받아서 본문 텍스트만 뽑아내는 역할만 하는 파일.

웹페이지 HTML에는 광고, 메뉴, 관련기사 링크 같은 게 섞여 있어서
그냥 통째로 가져오면 안 된다. `trafilatura`라는 라이브러리가
"이 페이지에서 진짜 기사 본문이 어디인지"를 자동으로 판단해서 뽑아준다.

주의: 로그인이 필요한 기사, 자바스크립트로만 렌더링되는 사이트,
크롤링을 막아둔 사이트는 실패할 수 있다. 그런 경우엔 파일 업로드 방식을 쓰면 된다.
"""

import trafilatura


def fetch_article_text(url: str) -> str:
    """URL에서 기사 본문 텍스트를 추출한다. 실패하면 예외를 던진다."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError("이 URL에 접속할 수 없었어요. 주소가 맞는지 확인해주세요.")

    text = trafilatura.extract(downloaded)
    if not text or len(text) < 100:
        raise RuntimeError(
            "이 페이지에서 본문을 뽑아내지 못했어요. "
            "로그인이 필요한 기사이거나 크롤링이 막혀있을 수 있어요 — 파일 업로드로 시도해주세요."
        )
    return text
