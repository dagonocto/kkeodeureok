"""API를 호출할 때마다 실제로 쓴 토큰 수를 기록해서, 누적 비용을 추적하는 파일.

OpenAI 결제 대시보드에 직접 들어가지 않아도, 우리가 스스로 매 호출의 토큰 수를
`usage_log.csv`에 한 줄씩 남겨두면 "지금까지 총 얼마 썼는지"를 언제든 계산할 수 있다.

주의: web_search 도구 사용료는 response.usage에 안 잡히고, response.output 안에
"web_search_call" 항목이 몇 개 있는지로 직접 세야 한다 — 안 그러면 검색 비용이
비용 계산에서 통째로 빠진다.
"""

import csv
import os
from datetime import date

USAGE_FILE = "usage_log.csv"
PERPLEXITY_USAGE_FILE = "perplexity_usage_log.csv"

# gpt-5.4-mini 가격 (2026-08 기준, 1백만 토큰당 달러). 가격이 바뀌면 여기만 고치면 된다.
INPUT_PRICE_PER_1M = 0.75
OUTPUT_PRICE_PER_1M = 4.50

# web_search 도구 사용료 (2026-08 기준, 1회 검색당 달러 — $10 / 1,000회)
WEB_SEARCH_PRICE_PER_CALL = 0.01


def log_usage(input_tokens: int, output_tokens: int, search_calls: int = 0) -> float:
    """이번 호출의 토큰 수 + 검색 횟수를 파일에 한 줄 추가하고, 예상 비용(달러)을 돌려준다."""
    cost = (
        (input_tokens / 1_000_000 * INPUT_PRICE_PER_1M)
        + (output_tokens / 1_000_000 * OUTPUT_PRICE_PER_1M)
        + (search_calls * WEB_SEARCH_PRICE_PER_CALL)
    )
    file_exists = os.path.exists(USAGE_FILE)
    with open(USAGE_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "input_tokens", "output_tokens", "search_calls", "cost_usd"])
        writer.writerow([date.today().isoformat(), input_tokens, output_tokens, search_calls, f"{cost:.6f}"])
    return cost


def log_perplexity_usage(input_tokens: int, output_tokens: int, cost: float, preset: str = "high") -> float:
    """Perplexity 검색 1건을 기록한다.

    cost는 Perplexity API가 응답에서 직접 알려준 실제 비용(usage.cost.total_cost)이다 —
    가격표가 preset마다·모델마다 자꾸 바뀌어서, 우리가 따로 추정하지 않고 Perplexity가 계산해준
    값을 그대로 기록하는 쪽이 정확하다(perplexity_client.py가 못 받아오면 대략치로 대신 채워서 넘김).
    """
    file_exists = os.path.exists(PERPLEXITY_USAGE_FILE)
    with open(PERPLEXITY_USAGE_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "input_tokens", "output_tokens", "preset", "cost_usd"])
        writer.writerow([date.today().isoformat(), input_tokens, output_tokens, preset, f"{cost:.6f}"])
    return cost


def total_cost() -> float:
    """지금까지 기록된 모든 호출(OpenAI + Perplexity)의 비용을 더한 값(달러)을 돌려준다."""
    total = 0.0
    for path in (USAGE_FILE, PERPLEXITY_USAGE_FILE):
        if not os.path.exists(path):
            continue
        with open(path, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += float(row["cost_usd"])
    return total
