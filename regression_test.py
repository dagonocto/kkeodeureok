"""프롬프트를 고칠 때마다 돌려보는 회귀 테스트.

지금까지 실제로 문제가 됐던 기사 몇 개를 고정 세트로 저장해두고, 프롬프트를 수정한
직후 이 스크립트로 다시 돌려서 "예전에 고쳤던 문제가 이번 수정으로 재발하지 않았는지"를
확인한다. 뉴스가 없는 날에도 언제든 검증할 수 있게 하는 게 목적이다.

실제 분석 로직은 analysis_pipeline.py의 것을 그대로 가져다 쓴다 — app.py와 로직이
갈라지지 않게 하기 위해서다.

예전에는 이 스크립트가 카드를 그대로 출력만 하고, "겹치나? 배경 빠졌나?"는 매번 사람이
눈으로 읽고 판단했다. 재현이 불안정한 버그가 많아서 여러 번 돌려봐야 하는데, 그때마다
사람이 처음부터 다시 읽어야 하는 게 부담이었다 — 그래서 eval_checks.py로 두 가지 자동
점검을 추가했다: (1) 카드 임베딩 유사도로 "겹칠 수도 있는" 쌍을 표시, (2) 원문에서 뽑은
고유명사가 카드에 실제로 들어갔는지 대조해서 "배경 누락일 수도 있는" 이름을 표시. 둘 다
완전 자동 판정이 아니라 "여기부터 사람이 보자"는 필터다 — 최종 판단은 여전히 사람이
아래 원문 카드를 읽고 내린다.

Notion에는 저장하지 않는다 — 테스트 결과가 실제 기록에 섞이면 안 되니까.
usage_log.csv / perplexity_usage_log.csv에는 정상적으로 비용이 기록된다(실제 API
호출이라 OpenAI + Perplexity 양쪽 다 돈이 든다 — 임베딩·고유명사 추출 호출도 마찬가지).

사용법:
    python regression_test.py            # 전체 테스트 케이스 실행
    python regression_test.py 제주        # 제목에 "제주"가 들어간 케이스만 실행
"""

import sys
import tomllib

from openai import OpenAI

from analysis_pipeline import analyze_article
from eval_checks import extract_entities, find_overlapping_pairs, missing_entities
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
    (
        "박진영 미국출장 주의조치",
        "https://www.hankyung.com/article/2026090205607",
        [
            "'과거 썰'과 '싸움의 이유'가 국회 vs 위원회 논쟁을 프레임만 바꿔 반복하지 않는가"
            " (감독기관 지적 vs 당사자 해명 패턴 — 2026-09-02에 이 패턴을 놓쳐서 규칙을 보강함)",
            "박진영이 누구이고 왜 위원장이 됐는지, 대중문화교류위원회가 뭔지가 카드 어딘가에"
            " 실제로 담겼는가 (직함만 있고 설명 없는 인물이 사건 중심인 기사인데도"
            " '정보성'으로 잘못 분류돼 배경 조사가 통째로 빠졌던 적이 있음)",
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
            document_block,
            url,
            secrets["OPENAI_API_KEY"],
            secrets["PERPLEXITY_API_KEY"],
            secrets.get("NOTION_TOKEN"),
            secrets.get("GLOSSARY_DATA_SOURCE_ID"),
        )
    except Exception as e:  # noqa: BLE001 - 테스트 스크립트라 넓게 잡고 다음 케이스로 넘어감
        print(f"에러: {e}\n")
        return

    # 자동 점검 — 완전한 판정이 아니라 "여기부터 사람이 읽어보자"는 필터. 판정 자체를
    # 대신하는 게 아니라서 실패해도(예: 임베딩 API 오류) 전체 테스트를 죽이지 않는다.
    client = OpenAI(api_key=secrets["OPENAI_API_KEY"])
    try:
        overlap_pairs = find_overlapping_pairs(client, data["axes"])
        entities = extract_entities(client, article_text)
        missing = missing_entities(entities, data["axes"])
    except Exception as e:  # noqa: BLE001
        print(f"(자동 점검 중 오류 — 건너뜀: {e})")
        overlap_pairs, entities, missing = [], [], []

    print(f"(호출 비용: 약 ${cost:.4f})")
    print(f"제목: {data['title']}")

    print("\n--- 자동 점검 ---")
    if overlap_pairs:
        for i, j, sim in overlap_pairs:
            title_i, title_j = data["axes"][i]["title"], data["axes"][j]["title"]
            print(f"  ⚠️ 중복 의심 (유사도 {sim:.2f}): [{title_i}] ↔ [{title_j}] — 사람이 다시 확인")
    else:
        print("  ✅ 카드끼리 겹침 의심 없음")
    if entities:
        if missing:
            print(f"  ⚠️ 카드에 안 보이는 원문 고유명사: {', '.join(missing)} — 배경 누락일 수 있음, 사람이 다시 확인")
        else:
            print(f"  ✅ 원문 고유명사 {len(entities)}개 모두 카드에 등장")
    else:
        print("  (고유명사 추출 실패 — 이 점검은 건너뜀)")

    print("\n--- 카드 원문 ---")
    for axis in data["axes"]:
        icon = "⚠️" if axis["sensitive"] else "  "
        print(f"\n{icon} [{axis['family']}] {axis['title']}")
        print(axis["explanation"])
        if axis.get("talk_line"):
            print(f"  💬 {axis['talk_line']}")
    print()


def main():
    # Windows 콘솔 기본 인코딩(cp949)은 "—" 같은 유니코드 문자를 못 뱉어서, 파일로
    # 리다이렉트하든 콘솔에 바로 찍든 UnicodeEncodeError로 죽는 경우가 있었다 — UTF-8로
    # 강제한다.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
