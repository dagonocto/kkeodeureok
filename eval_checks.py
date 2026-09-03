"""회귀 테스트에서 카드 중복·배경 누락을 자동으로 점검하는 보조 함수들.

지금까지는 프롬프트를 고칠 때마다 사람이 카드를 눈으로 읽고 "이거 겹치나? 배경 빠졌나?"를
매번 새로 판단해야 했다(재현이 불안정해서 여러 번 돌려봐야 하는데, 그때마다 사람이 다시
읽어야 함). 아래 두 신호는 그 판단을 완전히 대신하는 게 아니라 — 특히 유사도 점수는 애매한
경우가 많아서 — "이 카드 세트는 한 번 더 사람이 봐야 한다"는 걸 자동으로 표시해주는
필터다. 최종 판단은 여전히 regression_test.py를 돌리는 사람이 한다.
"""

import itertools
import json
import math

from openai import OpenAI

EMBEDDING_MODEL = "text-embedding-3-small"

# 카드 두 개의 explanation 임베딩 코사인 유사도가 이 값을 넘으면 "겹칠 수도 있다"고
# 표시한다. 아직 실측으로 보정한 값이 아니라 보수적으로 잡은 시작값이다 — 오탐(사실은
# 안 겹치는데 걸림)·누락(진짜 겹치는데 안 걸림)이 계속 보이면 그때그때 조정한다.
OVERLAP_SIMILARITY_THRESHOLD = 0.82

# 고유명사 추출은 사실 나열만 시키는 단순한 작업이라 gpt-5.4-mini로 충분하다 — 굳이
# "이게 배경 설명이 필요한가"까지 판단시키지 않는다(그건 우리가 계속 못 미더워하던 바로 그
# 판단이라, 추출 단계에서는 최대한 단순하게 "고유명사 목록만" 뽑게 하고 판단은 recall
# 체크(missing_entities)로 넘긴다).
ENTITY_EXTRACTION_MODEL = "gpt-5.4-mini"

ENTITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "기사 본문에 나오는 구체적 고유명사(사람 이름, 기관·조직명, 사건·행사·정책명)만. 일반명사·흔한 지명은 뺀다.",
        }
    },
}

ENTITY_EXTRACTION_PROMPT = """기사 본문에서 사람 이름, 기관·조직명, 사건·행사·정책명 같은
구체적 고유명사를 모두 뽑아라. "서울", "정부"처럼 너무 일반적인 표현은 빼고, 이 기사를
이해하려면 "이게 뭔지/누군지" 알아야 하는 고유명사 위주로 뽑는다. 각 항목은 기사 본문에
실제로 쓰인 표기 그대로 적는다."""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_overlapping_pairs(client: OpenAI, axes: list[dict]) -> list[tuple[int, int, float]]:
    """카드(축)끼리 explanation 임베딩의 코사인 유사도를 재서, 기준을 넘는 쌍을 돌려준다.

    (i, j, similarity) 튜플 리스트 — i, j는 axes 리스트 안 인덱스.
    """
    if len(axes) < 2:
        return []
    texts = [axis["explanation"] for axis in axes]
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    vectors = [item.embedding for item in response.data]

    flagged = []
    for i, j in itertools.combinations(range(len(axes)), 2):
        sim = cosine_similarity(vectors[i], vectors[j])
        if sim >= OVERLAP_SIMILARITY_THRESHOLD:
            flagged.append((i, j, sim))
    return flagged


def extract_entities(client: OpenAI, article_text: str) -> list[str]:
    """기사 원문에서 배경 설명이 필요할 만한 고유명사(인물·기관·행사명)를 뽑는다."""
    response = client.responses.create(
        model=ENTITY_EXTRACTION_MODEL,
        max_output_tokens=1500,
        input=[
            {"role": "system", "content": ENTITY_EXTRACTION_PROMPT},
            {"role": "user", "content": article_text[:6000]},
        ],
        text={"format": {"type": "json_schema", "name": "entities", "strict": True, "schema": ENTITY_SCHEMA}},
    )
    if response.status != "completed":
        return []
    return json.loads(response.output_text)["entities"]


def missing_entities(entities: list[str], axes: list[dict]) -> list[str]:
    """추출된 고유명사 중, 완성된 카드(제목+본문) 어디에도 안 나오는 것들을 돌려준다."""
    all_card_text = "\n".join(axis["title"] + "\n" + axis["explanation"] for axis in axes)
    return [e for e in entities if e and e not in all_card_text]
