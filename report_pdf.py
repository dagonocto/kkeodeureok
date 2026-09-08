"""꺼드럭 분석 결과를 A4 PDF 리포트로 만드는 스크립트.

analyze_article()가 돌려주는 결과(dict)를 받아서, 요약·포인트별 해설·출처를
A4 한 장(내용이 길면 여러 장) PDF로 만든다. 카드뉴스(cardnews.py, 화면 공유용
PNG)와 달리 fpdf2로 실제 텍스트를 그려서, 이 PDF는 글자를 선택·검색할 수 있고
인쇄해서 읽기에도 적합하다.

API 호출이 전혀 없는 순수 로컬 렌더링이라 비용이 들지 않는다.

폰트는 카드뉴스와 같은 이유로 리포에 번들된 assets/fonts/Pretendard-*.otf를
쓴다(OS에 뭐가 깔려있든 상관없이 항상 렌더링되게). 이 폰트엔 이모지가 없어서
카드뉴스처럼 이모지를 쓰지 않고, 대신 "[주의]" 같은 대괄호 텍스트 표시로
대신한다 — 인쇄물에서는 오히려 이쪽이 더 깔끔하다.

사용법:
    python report_pdf.py --sample --out report.pdf
    python report_pdf.py --json article.json --out report.pdf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fpdf import FPDF

_ASSET_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_REGULAR = str(_ASSET_FONT_DIR / "Pretendard-Regular.otf")
FONT_BOLD = str(_ASSET_FONT_DIR / "Pretendard-Bold.otf")
# Pretendard는 한글 전용이라 한자가 없다. 그런데 "출처(references)"에 들어가는 기사
# 제목은 실제 언론사 헤드라인을 그대로 복사해오는 부분이라, "美 출장"처럼 국가명
# 약칭 등에 한자가 섞여 나오는 경우가 실제로 있다(--json 테스트로 재현 확인).
# Noto Sans KR에서 그런 관용 한자만 100자 추려 잘라낸 아주 작은(30KB) 폴백
# 폰트를 fpdf2의 fallback font로 등록해서, Pretendard에 없는 글자만 이걸로
# 대신 그린다 — 원본 배포판(10MB+)을 통째로 넣지 않아도 된다.
FONT_HANJA_FALLBACK = str(_ASSET_FONT_DIR / "NotoSansKR-HanjaFallback.ttf")

# app.py CSS 변수와 같은 톤(0~255 RGB로 변환).
INK = (22, 32, 58)
INK_DIM = (87, 96, 125)
INK_FAINT = (154, 163, 189)
ACCENT = (47, 111, 237)
ACCENT_TINT = (233, 240, 254)
BORDER = (205, 214, 232)

MARGIN = 18


class ReportPDF(FPDF):
    """머리글 없이 쓰고, 바닥글(페이지 번호 + 브랜드)만 모든 페이지에 자동으로 붙인다."""

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Pretendard", "", 8)
        self.set_text_color(*INK_FAINT)
        self.cell(0, 10, "꺼드럭", align="L")
        self.set_xy(-MARGIN - 20, -15)
        self.cell(20, 10, f"{self.page_no()}", align="R")


def _register_fonts(pdf: FPDF) -> None:
    pdf.add_font("Pretendard", "", FONT_REGULAR)
    pdf.add_font("Pretendard", "B", FONT_BOLD)
    pdf.add_font("NotoSansKRFallback", "", FONT_HANJA_FALLBACK)
    # exact_match=False: 지금 글씨가 굵게(B) 설정돼 있어도, 폴백 폰트엔 굵은 버전을
    # 안 만들어놨으니 있는 걸(가는 글씨) 그냥 쓰게 한다 — 한자 한두 글자 굵기가
    # 안 맞는 것보다, 그 글자가 아예 안 보이는 쪽이 훨씬 나쁘다.
    pdf.set_fallback_fonts(["NotoSansKRFallback"], exact_match=False)


def _ensure_space(pdf: FPDF, height: float) -> None:
    """이 높이만큼 그릴 공간이 이 페이지에 안 남아있으면 미리 다음 페이지로 넘긴다.

    talk_line 말풍선은 배경색 박스(rect)를 먼저 그리고 그 위에 글자(multi_cell)를
    쓰는 방식인데, rect()는 자동 페이지 넘김을 모른다 — 그래서 박스를 그린 직후
    글자 쪽에서만 자동으로 다음 페이지로 넘어가면, 박스와 글자가 서로 다른
    페이지에 떨어지는 버그가 생긴다(실제로 --sample 렌더링에서 재현됨). 박스를
    그리기 전에 미리 공간을 확인해서 필요하면 페이지를 넘겨, 박스와 글자가 항상
    같은 페이지에 붙어있게 한다.
    """
    if pdf.get_y() + height > pdf.page_break_trigger:
        pdf.add_page()


def _section_heading(pdf: FPDF, text: str) -> None:
    pdf.ln(4)
    pdf.set_font("Pretendard", "B", 13)
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*BORDER)
    pdf.line(pdf.get_x(), pdf.get_y() + 1, pdf.get_x() + pdf.epw, pdf.get_y() + 1)
    pdf.ln(4)


def _axis_section(pdf: FPDF, axis: dict) -> None:
    # 배지+제목 정도는 최소한 안 잘리게 — 본문까지 완벽히 예측하긴 어려워서
    # (길이가 들쭉날쭉) 배지·제목 두 줄 분량만 최소로 보장한다.
    _ensure_space(pdf, 30)
    # family 배지 — 카드뉴스의 이모지 아이콘 대신 accent 색 텍스트로 표시한다.
    pdf.set_font("Pretendard", "B", 9)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 6, axis.get("family", ""), new_x="LMARGIN", new_y="NEXT")

    title = axis.get("title", "")
    if axis.get("sensitive"):
        title = f"[주의] {title}"
    pdf.set_font("Pretendard", "B", 13)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 7.5, title, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(1)

    pdf.set_font("Pretendard", "", 10.5)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 6.3, axis.get("explanation", ""), new_x="LMARGIN", new_y="NEXT", align="L")

    if axis.get("confidence") == "low":
        pdf.ln(1)
        pdf.set_font("Pretendard", "", 8.5)
        pdf.set_text_color(*INK_FAINT)
        pdf.cell(0, 5, "확실하지 않음 — 확인 필요", new_x="LMARGIN", new_y="NEXT")

    talk_line = axis.get("talk_line")
    if talk_line:
        pdf.ln(2)
        pdf.set_font("Pretendard", "B", 10)
        # 먼저 높이를 재기 위해 텍스트를 실제로 그리지 않고 줄 수만 계산한다.
        quote = f'"{talk_line}"'
        lines = pdf.multi_cell(pdf.epw - 12, 6, quote, dry_run=True, output="LINES")
        box_h = max(len(lines), 1) * 6 + 6
        _ensure_space(pdf, box_h)
        box_x, box_y = pdf.get_x(), pdf.get_y()
        pdf.set_fill_color(*ACCENT_TINT)
        pdf.rect(box_x, box_y, pdf.epw, box_h, style="F")
        pdf.set_xy(box_x + 6, box_y + 3)
        pdf.set_text_color(*ACCENT)
        pdf.multi_cell(pdf.epw - 12, 6, quote, new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.set_xy(box_x, box_y + box_h)

    refs = axis.get("references") or []
    if refs:
        pdf.ln(2)
        pdf.set_font("Pretendard", "", 8.5)
        pdf.set_text_color(*INK_FAINT)
        for ref in refs:
            line = f"출처: {ref.get('source', '')} · {ref.get('title', '')}"
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.ln(6)


def build_report_pdf(data: dict, out_path: str | Path) -> Path:
    """data(analyze_article()의 결과와 같은 구조)를 받아 A4 PDF 리포트를 out_path에 저장한다."""
    pdf = ReportPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    _register_fonts(pdf)
    pdf.add_page()

    pdf.set_font("Pretendard", "B", 12)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 7, "꺼드럭 분석 리포트", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.set_font("Pretendard", "B", 19)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 10, data.get("title", ""), new_x="LMARGIN", new_y="NEXT", align="L")

    meta = f"{data.get('category', '')} · {data.get('source_name', '')}"
    date = data.get("published_date")
    if date:
        meta += f" · {date}"
    pdf.set_font("Pretendard", "", 9.5)
    pdf.set_text_color(*INK_DIM)
    pdf.cell(0, 6, meta, new_x="LMARGIN", new_y="NEXT")
    source_url = data.get("source_url")
    if source_url:
        pdf.set_text_color(*ACCENT)
        pdf.cell(0, 6, source_url, new_x="LMARGIN", new_y="NEXT", link=source_url)

    _section_heading(pdf, "요약")
    pdf.set_font("Pretendard", "", 10.5)
    pdf.set_text_color(*INK)
    for i, point in enumerate(data.get("summary", []), start=1):
        pdf.multi_cell(0, 6.3, f"{i}. {point}", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(1)

    axes = data.get("axes") or []
    if axes:
        _section_heading(pdf, "포인트별 해설")
        for axis in axes:
            _axis_section(pdf, axis)

    references = data.get("references") or []
    _section_heading(pdf, "더 파보고 싶으면")
    if not references:
        pdf.set_font("Pretendard", "", 10)
        pdf.set_text_color(*INK_DIM)
        pdf.cell(0, 6, "없음", new_x="LMARGIN", new_y="NEXT")
    for ref in references:
        pdf.set_font("Pretendard", "", 10)
        pdf.set_text_color(*INK)
        line = f"{ref.get('source', '')} · {ref.get('title', '')}"
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT", align="L")
        url = ref.get("url")
        if url:
            pdf.set_font("Pretendard", "", 8.5)
            pdf.set_text_color(*ACCENT)
            pdf.cell(0, 5, url, new_x="LMARGIN", new_y="NEXT", link=url)
        pdf.ln(1)

    out_path = Path(out_path)
    pdf.output(str(out_path))
    return out_path


# ============================================================
# 예시 데이터 (--sample 용) — cardnews.py의 SAMPLE_DATA를 그대로 재사용한다.
# ============================================================

def _sample_data() -> dict:
    import cardnews

    return cardnews.SAMPLE_DATA


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="꺼드럭 분석 결과를 A4 PDF 리포트로 만든다.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="analyze_article() 결과를 저장해둔 JSON 파일 경로")
    src.add_argument("--sample", action="store_true", help="내장 예시 데이터로 테스트 렌더링")
    parser.add_argument("--out", default="report.pdf", help="결과 PDF 경로 (기본: report.pdf)")
    args = parser.parse_args()

    data = _sample_data() if args.sample else json.loads(Path(args.json).read_text(encoding="utf-8"))
    out_path = build_report_pdf(data, args.out)
    print(f"저장했어요 -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
