"""프롬프트를 고칠 때마다 돌려보는 회귀 테스트.

지금까지 실제로 문제가 됐던 기사 몇 개를 고정 세트로 저장해두고, 프롬프트를 수정한
직후 이 스크립트로 다시 돌려서 "예전에 고쳤던 문제가 이번 수정으로 재발하지 않았는지"를
확인한다. 뉴스가 없는 날에도 언제든 검증할 수 있게 하는 게 목적이다.

실제 분석 로직은 analysis_pipeline.py의 것을 그대로 가져다 쓴다 — app.py와 로직이
갈라지지 않게 하기 위해서다.

Notion에는 저장하지 않는다 — 테스트 결과가 실제 기록에 섞이면 안 되니까.
usage_log.csv / perplexity_usage_log.csv에는 정상적으로 비용이 기록된다(실제 API
호출이라 OpenAI + Perplexity 양쪽 다 돈이 든다).

사용법:
    python regression_test.py            # 전체 테스트 케이스 실행
    python regression_test.py 제주        # 제목에 "제주"가 들어간 케이스만 실행
"""

import sys
import tomllib

from analysis_pipeline import analyze_article
from fetch_article import fetch_article_text

# (기사 URL, 이 기사에서 과거에 어떤 문제가 있었는지 — 재발했는지 직접 눈으로 확인할 포인트)
TEST_CASES = [
    (
        "제주 실종 (허위 종결)",
        "https://n.news.naver.com/article/023/0003994356?cds=news_media_pc",
        [
            "실제 대립이 없는데 '가족 vs 경찰' 갈등을 지어내지 않는가",
            "'허위 종결'처럼 뜻이 짐작되는 쉬운 용어까지 설명하지 않는가",
            "장미란씨 본인의 실종 경위(날짜·시각·정황)가 실제로 담겼는가",
            "'이거 오해였음' 축이 사건 경위 축과 같은 내용을 재탕하지 않는가",
            "'왜 수색이 꼬이나' 같은 인과관계 설명에 법령/매뉴얼/전문가 등 출처가 있는가",
        ],
    ),
    (
        "노웅래 무죄 공방",
        "https://n.news.naver.com/mnews/article/469/0000949396",
        [
            "'불법 정치자금 혐의'라고만 하지 않고 구체적 액수(6천만원 등)가 본문에 들어갔는가",
            "사건 실체보다 정치적 공방으로 먼저 건너뛰지 않았는가",
            "문장 끝에 `}],` 같은 깨진 문자가 없는가",
        ],
    ),
    (
        "서울 집값 규제",
        "https://n.news.naver.com/article/469/0000949061",
        [
            "'원리 뽀개기'·'싸움의 이유'·'이거 오해였음'이 같은 인사이트를 반복하지 않는가",
            "'정부는 이렇게 보고 시장은 저렇게 본다'로만 끝내지 않고, 사실관계 대립은 결론을 내는가",
        ],
    ),
    (
        "근로소득 양도소득세 격차",
        "https://n.news.naver.com/mnews/article/469/0000949529",
        [
            "축이 4개를 넘지 않는가 (한때 7개까지 나온 적 있음 — maxItems 스키마 제약으로 방지)",
            "'용어 뽀개기' 축이 여러 개로 쪼개져서 같은 용어(실효세율·결정세액 등)를 두 번 정의하지 않는가",
            "한 문장에 서로 다른 항목의 숫자를 3개 넘게 몰아넣지 않는가",
            "각 문단에 그 내용에 맞는 굵은 소제목이 붙어 있는가",
        ],
    ),
]


def load_secrets() -> dict:
    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def run_case(secrets: dict, name: str, url: str, watch_for: list[str]) -> None:
    print("=" * 80)
    print(f"[{name}] {url}")
    print("확인할 점:")
    for point in watch_for:
        print(f"  - {point}")
    print()

    try:
        article_text = fetch_article_text(url)
        document_block = {"type": "input_text", "text": article_text}
        data, cost = analyze_article(
            document_block, url, secrets["OPENAI_API_KEY"], secrets["PERPLEXITY_API_KEY"]
        )
    except Exception as e:  # noqa: BLE001 - 테스트 스크립트라 넓게 잡고 다음 케이스로 넘어감
        print(f"에러: {e}\n")
        return

    print(f"(호출 비용: 약 ${cost:.4f})")
    print(f"제목: {data['title']}")
    for axis in data["axes"]:
        icon = "⚠️" if axis["sensitive"] else "  "
        print(f"\n{icon} [{axis['family']}] {axis['title']}")
        print(axis["explanation"])
        if axis.get("talk_line"):
            print(f"  💬 {axis['talk_line']}")
    print()


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else None
    secrets = load_secrets()

    cases = [c for c in TEST_CASES if keyword is None or keyword in c[0]]
    if not cases:
        print(f"'{keyword}'가 제목에 들어간 테스트 케이스가 없어요.")
        return

    for name, url, watch_for in cases:
        run_case(secrets, name, url, watch_for)


if __name__ == "__main__":
    main()
