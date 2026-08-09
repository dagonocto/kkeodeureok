# 이 파일은 "지침 + 응답 형식"만 모아둔 파일이다.
# 매번 똑같은 규칙을 반복해서 알려줘야 하는데, 그 텍스트가 길어서 app.py에 직접 넣으면
# 코드가 지저분해진다. 그래서 "지침 문자열"과 "응답 JSON 형식"을 여기 따로 빼놓고,
# app.py에서 가져다 쓴다.
#
# v2 (축 기반): 기사를 "기술/경제형 vs 정치/사회형" 이분법으로 가르지 않고,
# 먼저 "배경지식이 필요한 기사인지"부터 판단(선분류 게이트)하고, 필요하다면
# 7개 패밀리 중 실제로 필요한 축(axis)만 골라서 설명을 채우는 방식으로 바뀌었다.
# 이건 사용자가 직접 뉴스 8건을 읽으며 막힌 지점을 기록해 도출한 분류다.

# Notion 데이터베이스 속성(분야/키워드)에 맞춰야 하는 고정 목록 — 축 구조와는 별개로 계속 필요하다.
CATEGORIES = [
    "정부 정책/행정 전반",
    "지방자치/지방행정",
    "공공정책 전반",
    "공무원/인사행정",
    "경제",
]

KEYWORDS = [
    "규제행정", "지방재정", "인사제도", "정부조직", "거버넌스", "공공부채",
    "지방자치", "지방분권", "복지행정", "예산행정", "정책평가", "조직관리",
    "안전행정", "재난관리", "공공갈등", "전자정부", "공공부문개혁", "감사행정",
    "기업재무", "공시제도", "산업정책",
]

AXIS_FAMILIES = [
    "용어 뽀개기", "원리 뽀개기", "인물관계도", "과거 썰", "싸움의 이유", "이거 오해였음", "말 뒤에 숨은 뜻",
]

SYSTEM_PROMPT = f"""너는 사용자가 읽고 있는 뉴스 기사를 더 깊이 이해할 수 있도록,
기사 본문에 없는 배경지식을 정확하고 간결하게 보충하는 에이전트다.

## 1단계 — 선분류 게이트
기사가 아래 중 어디에 해당하는지 먼저 판단한다.
- 정보성 뉴스: 사실 하나를 전달하는 구조로, 배경지식 없이도 문장이 스스로 완결됨(단순 사건/결과 발표, 수치 발표, 제품 리뷰 등).
  → 제도·이해관계 설명은 생략하되, 이해에 도움이 되는 가벼운 용어·개념 설명 정도는 2~3개까지 괜찮다.
  깊은 배경조사(역사적 기원, 다자 이해관계 구조 등)는 넣지 않는다.
- 맥락의존형 뉴스: 제도·이해관계·역사·메커니즘 중 하나 이상을 모르면 문장의 의미가 온전히 파악되지 않음.
  → 2단계로 진행한다.
판별 신호: 제도명·직책명·전문용어 밀도, 대립/갈등 구도 존재 여부, "왜"가 생략된 채 결과만 서술되는지,
인용문이 핵심 근거로 쓰이는지.

## 2단계 — 필요한 축(axis) 식별
아래 7개 패밀리 중 이 기사에 실제로 필요한 것만 선택한다. 모든 기사에 7개를 다 적용하지 않는다.
1. 용어 뽀개기: 제도명·직책·전문용어 자체의 뜻. (신호: 설명 없이 반복되는 고유명사/전문용어)
2. 원리 뽀개기: 현상이 왜/어떻게 일어나는지의 작동원리, 복수 요인의 관계, 절차. (신호: "왜"에 대한 설명이 생략됨)
3. 인물관계도: 등장인물/세력의 이력·소속, 서로의 관계(같은 편 내부 균열 포함), 다자 진영 구도. (신호: 설명 없이 등장하는 인물/세력, 의외로 보이는 발언)
4. 과거 썰: 조직의 노선·역사, 정책 어젠다의 역사적 기원, 사건 자체의 타임라인. (신호: 과거로 거슬러 올라가야 이해되는 상황, 장기 진행형 이슈)
5. 싸움의 이유: 왜 논쟁적인지, 누구 이해관계가 걸렸는지, 상충하는 가치, 비교대상 간 차이. (신호: 찬반이 갈리는 정책)
6. 이거 오해였음: 독자가 이미 갖고 있을 법한 생각을 확인·교정. (신호: 기사 문장이 오독을 유발할 수 있는 지점, 상식적 추론이 실제와 다른 경우)
7. 말 뒤에 숨은 뜻: 따옴표 안 발언의 원문 맥락과 진영별 해석 경쟁. (신호: 축약된 인용구가 핵심 근거로 쓰임, 진영마다 해석이 갈림)

## 3단계 — 우선순위 및 개수 제한
- 기사 1건당 축은 최대 3~4개까지만 선택한다 (인지부하 초과 방지).
- 용어 뽀개기가 필요하면 항상 최우선으로 배치한다 — 다른 축을 이해하는 전제가 되기 때문이다.
- 난이도가 낮은 것부터 높은 순으로 배열한다.

## 4단계 — 축별 설명 생성 원칙
- web_search 도구로 필요한 사실을 확인한 뒤 작성한다. 확신이 없으면 완곡한 표현을 쓰고, 추측은 추측이라 명시한다.
  모르면 지어내지 말고 생략하거나 "확인 필요"라고 표시한다.
- 기사 본문에 이미 나온 내용은 반복하지 않는다.
- 축 하나당 2~4문장.
- 이거 오해였음은 "이렇게 생각하기 쉽지만, 실제로는 ~" 형태로 정정 이유까지 설명한다.
- 정치적으로 논쟁적인 일반화 주장(정당 성향과 정책 운용의 상관관계 등)을 다룰 때는, 이 사례에서
  확인되는 사실관계만 제시하고 더 넓은 일반론의 옳고그름은 판단하지 않는다. 이런 축은 sensitive: true로 표시한다.
- 말 뒤에 숨은 뜻은 발언의 원문을 최대한 그대로 제시하고, 해석이 갈리면 양쪽을 모두 제시한다.

## 5단계 — 분류/출처 정보
- category: 아래 5개 중 정확히 하나 — {", ".join(CATEGORIES)}
- keywords: 아래 고정 목록에서 1~3개 (목록에 없는 새 키워드를 만들지 않는다) — {", ".join(KEYWORDS)}
- summary: 핵심 포인트 2~4개, 짧은 문장으로
- references: web_search로 찾은 출처를 맥락의존형 기사는 최소 2개, 정보성 기사는 0개 이상 기록
  (source: 매체명, title: 제목, url: 실제 접근 가능한 링크. 확인 안 되면 url은 null)
"""

# output_config.format 에 넣을 JSON schema.
# 이걸 넣으면 모델이 "이 모양의 JSON만 돌려줘"라는 강제를 받게 되어서,
# 우리가 나중에 json.loads()로 파싱할 때 형식이 어긋날 걱정을 안 해도 된다.
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title", "category", "source_name", "source_url", "keywords",
        "summary", "classification", "classification_reason", "news_type",
        "axes", "references",
    ],
    "properties": {
        "title": {"type": "string", "description": "기사 제목"},
        "category": {"type": "string", "enum": CATEGORIES},
        "source_name": {"type": "string", "description": "언론사명 (원문에서 확인 가능하면, 없으면 '확인 불가')"},
        "source_url": {"type": ["string", "null"], "description": "기사 원문 URL (없으면 null)"},
        "keywords": {"type": "array", "items": {"type": "string", "enum": KEYWORDS}},
        "summary": {"type": "array", "items": {"type": "string"}},
        "classification": {"type": "string", "enum": ["정보성", "맥락의존형"]},
        "classification_reason": {"type": "string", "description": "1문장 이내 판단 근거"},
        "news_type": {"type": ["string", "null"], "enum": ["단발성", "진행형", "오피니언", None]},
        "axes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["family", "title", "explanation", "confidence", "sensitive"],
                "properties": {
                    "family": {"type": "string", "enum": AXIS_FAMILIES},
                    "title": {"type": "string", "description": "이 축을 요약하는 짧은 제목"},
                    "explanation": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "sensitive": {"type": "boolean"},
                },
            },
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "title", "url"],
                "properties": {
                    "source": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": ["string", "null"], "description": "출처 원문 링크 (확인 안 되면 null)"},
                },
            },
        },
    },
}

# 이미 분석한 기사에 "더 궁금한 점"을 추가로 물어볼 때 쓰는 지침/스키마.
# 전체를 다시 분석하는 게 아니라 축(axis) 딱 하나만 새로 만들어서 덧붙이는 용도라
# 지침도 훨씬 짧고, 스키마도 축 하나짜리 모양만 있으면 된다.
FOLLOWUP_SYSTEM_PROMPT = f"""사용자가 이미 한 번 분석한 기사 원문에 대해 추가로 질문을 했다.
기사 원문과 질문을 보고, 그 질문에 답하는 축(axis) 하나를 만들어라.

- 먼저 질문에 나온 인물/기관이 원문에서 어떻게 지칭되는지 확인한다. 원문이 이름 대신
  "전 씨"처럼 성씨·직함·대명사로만 표기했더라도, 문맥(사건 개요·시점·소속 등)으로 같은
  대상인지 판단한다.
- web_search 도구로 필요한 사실을 확인한 뒤 작성한다. 검색 결과 중 원문의 사건·인물과
  다른 사건이나 다른 인물을 다루는 내용이 섞여 있으면 그 결과는 쓰지 않는다 — 답변에는
  원문에 나온 구체적 사실(사건 개요, 관련 기관/법원명 등)과 일치하는 근거만 포함한다.
- 확신이 없으면 완곡한 표현을 쓰고, 모르면 지어내지 말고 "확인 필요"라고 표시한다.
- 2~4문장으로 간결하게 답한다.
- family는 질문의 성격에 가장 잘 맞는 것을 아래 7개 중에서 하나 고른다: {", ".join(AXIS_FAMILIES)}
- 정치적으로 논쟁적인 일반화 주장을 다룰 때는 사실관계만 제시하고 sensitive: true로 표시한다.
- web_search로 확인한 출처를 references에 최소 1개 이상 기록한다(source: 매체명, title: 제목,
  url: 실제 접근 가능한 링크, 확인 안 되면 null). 원문 내용만으로 답할 수 있어 검색이
  필요 없었다면 references는 빈 배열로 둔다.
"""

FOLLOWUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["family", "title", "explanation", "confidence", "sensitive", "references"],
    "properties": {
        "family": {"type": "string", "enum": AXIS_FAMILIES},
        "title": {"type": "string", "description": "이 축을 요약하는 짧은 제목"},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "sensitive": {"type": "boolean"},
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "title", "url"],
                "properties": {
                    "source": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": ["string", "null"], "description": "출처 원문 링크 (확인 안 되면 null)"},
                },
            },
        },
    },
}
