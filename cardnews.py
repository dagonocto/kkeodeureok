"""꺼드럭 카드뉴스 자동 생성 스크립트.

analyze_article()가 돌려주는 결과(dict) 하나를 받아서, 디자인 시안과 같은 톤의
인스타그램 카드뉴스 PNG 세트(1080x1350, 캐러셀 순서대로 번호 붙여 저장)를 만든다.

브라우저나 별도 렌더링 엔진 없이 Pillow만으로 직접 그린다. 한글 폰트는 리포에
직접 넣어둔 assets/fonts/Pretendard-*.otf(SIL OFL 라이선스, app.py 화면과 같은
폰트)를 최우선으로 쓴다 — 처음엔 OS에 이미 깔린 폰트(Windows 맑은 고딕 등)를
찾아 쓰는 방식이었는데, Streamlit Cloud(리눅스) 배포 환경엔 한글 폰트가 아예
없어서 배포 때마다 apt로 설치해야 했다. 그런데 그 서버의 apt 저장소 중 하나
(bullseye-security)가 유효기간이 지나 있어서 apt-get 자체가 실패했고, 그 바람에
카드뉴스뿐 아니라 앱 전체가 못 뜨는 사고로 이어진 적이 있다(2026-09-08) — 그래서
서버 상태에 기대지 않도록 폰트를 리포에 직접 넣는 방식으로 바꿨다. 이모지는
컬러 이모지 폰트 파일이 커서(수 MB) 여기엔 안 넣었다 — 없으면 그냥 이모지 없이
그려질 뿐, 실패하지는 않는다(draw_emoji가 조용히 건너뜀).

사용법:
    # 이미 Notion에 저장된 기사를 카드뉴스로
    python cardnews.py --notion-page-id <페이지 URL 또는 ID>

    # analyze_article()이 돌려준 결과를 JSON으로 저장해뒀다면
    python cardnews.py --json article.json

    # 폰트/레이아웃이 잘 나오는지만 확인하고 싶을 때 (내장 예시 데이터)
    python cardnews.py --sample
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

NOTION_VERSION = "2026-03-11"

# ---------- 브랜드 톤 (app.py의 CSS 변수와 동일한 값) ----------
BG = "#f6f8fc"
SURFACE = "#ffffff"
BORDER = "#cdd6e8"
INK = "#16203a"
INK_DIM = "#57607d"
INK_FAINT = "#9aa3bd"
ACCENT = "#2f6fed"
ACCENT_TINT = "#e9f0fe"

FAMILY_ICONS = {
    "용어 뽀개기": "📖",
    "원리 뽀개기": "⚙️",
    "인물관계도": "👥",
    "과거 썰": "🏛️",
    "싸움의 이유": "⚖️",
    "이거 오해였음": "🔍",
    "말 뒤에 숨은 뜻": "💬",
}
ICON_TO_FAMILY = {v: k for k, v in FAMILY_ICONS.items()}
DEFAULT_ICON = "💡"

W, H = 1080, 1350
PAGE_PAD = 56
CARD_PAD = 72
RADIUS = 32
BRAND_NAME = "꺼드럭"
BRAND_HANDLE = "@kkeodeureok"


# ============================================================
# 폰트 찾기
# ============================================================

_ASSET_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

FONT_REGULAR_CANDIDATES = [
    str(_ASSET_FONT_DIR / "Pretendard-Regular.otf"),
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]
FONT_BOLD_CANDIDATES = [
    str(_ASSET_FONT_DIR / "Pretendard-Bold.otf"),
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]
EMOJI_FONT_CANDIDATES = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _resolve_fonts() -> tuple[str, str, str | None]:
    regular = _first_existing(FONT_REGULAR_CANDIDATES)
    bold = _first_existing(FONT_BOLD_CANDIDATES)
    if not regular or not bold:
        raise RuntimeError(
            f"한글 폰트를 찾지 못했어요. 리포에 들어있어야 할 {_ASSET_FONT_DIR}/Pretendard-Regular.otf, "
            "Pretendard-Bold.otf가 없거나(git에 안 올라갔거나 지워짐), 그마저도 없으면 이 컴퓨터의 "
            "OS 폰트(Windows 맑은 고딕 등)도 못 찾은 상태예요."
        )
    emoji = _first_existing(EMOJI_FONT_CANDIDATES)
    return regular, bold, emoji


REGULAR_PATH, BOLD_PATH, EMOJI_PATH = _resolve_fonts()

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _load_ttc_kr(path: str, size: int) -> ImageFont.FreeTypeFont:
    """.ttc(폰트 여러 개가 묶인 파일)에서 한국어(KR) 서브폰트를 찾아 돌려준다.

    맑은 고딕처럼 폰트가 파일 하나에 하나만 들어있는 경우(.ttf/.otf)는 이 함수를
    거치지 않고 바로 로드한다 — Noto Sans CJK처럼 여러 언어가 한 파일에 묶인
    .ttc를 쓸 때만 필요한 처리.
    """
    for idx in range(6):
        try:
            f = ImageFont.truetype(path, size, index=idx)
        except Exception:
            break
        if "KR" in f.getname()[0]:
            return f
    return ImageFont.truetype(path, size, index=0)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = BOLD_PATH if bold else REGULAR_PATH
    key = (path, size)
    if key not in _font_cache:
        if path.lower().endswith(".ttc"):
            _font_cache[key] = _load_ttc_kr(path, size)
        else:
            _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


_emoji_cache: dict[tuple[str, int], Image.Image | None] = {}


def draw_emoji(base_img: Image.Image, emoji_char: str, xy: tuple[int, int], size: int) -> None:
    """이모지를 base_img 위 (x, y)(좌상단 기준)에 size x size로 그린다.

    이모지 폰트는 정해진 몇 가지 크기(strike)로만 그릴 수 있는 컬러 비트맵 폰트라
    바로 원하는 크기로 요청하면 실패하는 경우가 많다 — 그려지는 크기 후보를
    여러 개 시도해서 성공한 걸 원하는 크기로 리사이즈한다. 폰트를 아예 못 찾았거나
    끝까지 실패하면 자리만 비우고 넘어간다(카드뉴스 생성 자체가 멈추면 안 되므로).
    """
    if not EMOJI_PATH:
        return
    key = (emoji_char, size)
    if key not in _emoji_cache:
        glyph = None
        for strike in (size, 160, 136, 128, 109, 96, 76, 64, 48, 36, 32):
            try:
                f = ImageFont.truetype(EMOJI_PATH, strike)
                tmp = Image.new("RGBA", (strike * 2, strike * 2), (0, 0, 0, 0))
                d = ImageDraw.Draw(tmp)
                d.text((strike // 2, strike // 2), emoji_char, font=f, embedded_color=True)
                bbox = tmp.getbbox()
                if bbox:
                    glyph = tmp.crop(bbox).resize((size, size), Image.LANCZOS)
                    break
            except Exception:
                continue
        _emoji_cache[key] = glyph
    glyph = _emoji_cache[key]
    if glyph is not None:
        base_img.alpha_composite(glyph, xy)


# ============================================================
# 그리기 유틸
# ============================================================

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def wrap_text(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """max_width 안에 들어가도록 줄바꿈한다. 공백 기준으로 먼저 자르고, 공백이 없어서
    한 덩어리가 너무 길면(한글은 종종 그렇다) 글자 단위로도 자른다."""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        words = para.split(" ")
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=f) <= max_width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
                cur = ""
            if draw.textlength(w, font=f) <= max_width:
                cur = w
            else:
                chunk = ""
                for ch in w:
                    if draw.textlength(chunk + ch, font=f) <= max_width:
                        chunk += ch
                    else:
                        lines.append(chunk)
                        chunk = ch
                cur = chunk
        if cur:
            lines.append(cur)
    return lines


def fit_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    bold: bool = False,
    line_height_ratio: float = 1.55,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """실제 기사 데이터는 길이가 들쭉날쭉해서, 정해진 폰트 크기로는 카드 밖으로 넘칠 수 있다.
    start_size부터 min_size까지 크기를 낮춰가며 max_height 안에 들어가는 첫 크기를 쓴다.
    그래도 안 들어가면 min_size로 넘치더라도 그린다(잘리는 것보다는 낫다)."""
    size = start_size
    while size >= min_size:
        f = font(size, bold=bold)
        lines = wrap_text(draw, text, f, max_width)
        line_height = int(size * line_height_ratio)
        if len(lines) * line_height <= max_height or size == min_size:
            return f, lines, line_height
        size -= 2
    f = font(min_size, bold=bold)
    return f, wrap_text(draw, text, f, max_width), int(min_size * line_height_ratio)


def draw_lines(draw, xy, lines, f, fill, line_height) -> int:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=f, fill=fill)
        y += line_height
    return y


def new_card(img_base: Image.Image | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple[int, int, int, int]]:
    """배경 + 그림자 + 흰 카드 프레임을 그리고, 카드 안쪽 콘텐츠 영역 좌표를 돌려준다."""
    img = Image.new("RGBA", (W, H), hex_to_rgb(BG) + (255,))
    cx0, cy0, cx1, cy1 = PAGE_PAD, PAGE_PAD, W - PAGE_PAD, H - PAGE_PAD

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([cx0, cy0 + 10, cx1, cy1 + 14], radius=RADIUS, fill=(22, 32, 58, 40))
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))
    img.alpha_composite(shadow)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [cx0, cy0, cx1, cy1], radius=RADIUS,
        fill=hex_to_rgb(SURFACE) + (255,), outline=hex_to_rgb(BORDER) + (255,), width=2,
    )
    content_box = (cx0 + CARD_PAD, cy0 + CARD_PAD, cx1 - CARD_PAD, cy1 - CARD_PAD)
    return img, d, content_box


def draw_pill(img, d, xy, text, icon=None) -> int:
    """accent-tint 배경의 알약 모양 배지. 돌려주는 값은 배지 다음 y 좌표."""
    x, y = xy
    f = font(28, bold=True)
    label = f"{icon}  {text}" if icon and not EMOJI_PATH else text
    pad_x, pad_y = 28, 14
    text_w = d.textlength(text, font=f) + (44 if icon and EMOJI_PATH else 0)
    text_h = 34
    d.rounded_rectangle(
        [x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2],
        radius=999, fill=hex_to_rgb(ACCENT_TINT) + (255,),
    )
    tx = x + pad_x
    if icon and EMOJI_PATH:
        draw_emoji(img, icon, (int(tx), int(y + pad_y - 2)), 34)
        tx += 44
    d.text((tx, y + pad_y), text, font=f, fill=hex_to_rgb(ACCENT))
    return int(y + text_h + pad_y * 2)


def draw_footer(img, d, page_no: int, total: int) -> None:
    y = H - PAGE_PAD - CARD_PAD - 10
    x = PAGE_PAD + CARD_PAD
    if EMOJI_PATH:
        draw_emoji(img, "😎", (x, y - 4), 30)
        x += 40
    f = font(22, bold=True)
    d.text((x, y), BRAND_NAME, font=f, fill=hex_to_rgb(ACCENT))
    counter = f"{page_no} / {total}"
    fc = font(22, bold=False)
    cw = d.textlength(counter, font=fc)
    d.text((W - PAGE_PAD - CARD_PAD - cw, y), counter, font=fc, fill=hex_to_rgb(INK_FAINT))


# ============================================================
# 슬라이드
# ============================================================

def draw_cover(data: dict, page_no: int, total: int) -> Image.Image:
    img, d, (left, top, right, bottom) = new_card()
    max_w = right - left

    row_y = top
    if EMOJI_PATH:
        draw_emoji(img, "😎", (left, row_y - 4), 40)
    d.text((left + 52, row_y), BRAND_NAME, font=font(34, bold=True), fill=hex_to_rgb(ACCENT))
    handle_f = font(24, bold=True)
    hw = d.textlength(BRAND_HANDLE, font=handle_f)
    d.text((right - hw, row_y + 6), BRAND_HANDLE, font=handle_f, fill=hex_to_rgb(INK_DIM))

    y = row_y + 76
    y = draw_pill(img, d, (left, y), data.get("category", ""))
    y += 28

    title_f, title_lines, title_lh = fit_block(d, data.get("title", ""), max_w, 340, 72, 44, bold=True, line_height_ratio=1.22)
    y = draw_lines(d, (left, y), title_lines, title_f, hex_to_rgb(INK), title_lh)
    y += 28

    subtitle = "몰라도 괜찮아요 — 다음 장부터 천천히 풀어드릴게요"
    sub_lines = wrap_text(d, subtitle, font(32, bold=False), max_w)
    draw_lines(d, (left, y), sub_lines, font(32, bold=False), hex_to_rgb(INK_DIM), 46)

    src_y = bottom - 60
    source_line = data.get("source_name", "")
    date = data.get("published_date") or ""
    if date:
        source_line = f"{source_line} · {date}"
    d.text((left, src_y - 26), source_line, font=font(22, bold=False), fill=hex_to_rgb(INK_DIM))

    counter_f = font(22, bold=False)
    counter = f"{page_no} / {total}"
    swipe_f = font(28, bold=True)
    swipe = "스와이프 →"
    sw_w = d.textlength(swipe, font=swipe_f)
    d.text((right - sw_w, src_y - 2), swipe, font=swipe_f, fill=hex_to_rgb(ACCENT))
    cw = d.textlength(counter, font=counter_f)
    d.text((right - sw_w - 24 - cw, src_y + 2), counter, font=counter_f, fill=hex_to_rgb(INK_FAINT))

    return img.convert("RGB")


def draw_summary(data: dict, page_no: int, total: int) -> Image.Image:
    img, d, (left, top, right, bottom) = new_card()
    max_w = right - left

    y = top
    if EMOJI_PATH:
        draw_emoji(img, "📌", (left, y), 40)
    d.text((left + 52, y), "요약", font=font(44, bold=True), fill=hex_to_rgb(INK))
    y += 96

    for i, point in enumerate(data.get("summary", []), start=1):
        circle_d = 48
        d.ellipse([left, y, left + circle_d, y + circle_d], fill=hex_to_rgb(ACCENT_TINT) + (255,))
        num_f = font(24, bold=True)
        nw = d.textlength(str(i), font=num_f)
        d.text((left + circle_d / 2 - nw / 2, y + 9), str(i), font=num_f, fill=hex_to_rgb(ACCENT))

        body_f, lines, lh = fit_block(d, point, max_w - circle_d - 24, 220, 36, 26, line_height_ratio=1.5)
        draw_lines(d, (left + circle_d + 24, y + 4), lines, body_f, hex_to_rgb(INK), lh)
        y += max(circle_d, len(lines) * lh) + 40

    draw_footer(img, d, page_no, total)
    return img.convert("RGB")


def draw_axis(axis: dict, page_no: int, total: int) -> Image.Image:
    img, d, (left, top, right, bottom) = new_card()
    max_w = right - left

    family = axis.get("family", "")
    icon = FAMILY_ICONS.get(family, DEFAULT_ICON)
    y = draw_pill(img, d, (left, top), family, icon=icon)
    y += 24

    title_text = axis.get("title", "")
    if axis.get("sensitive"):
        title_text = f"\u26a0\ufe0f {title_text}"
    talk_line = axis.get("talk_line")
    footer_zone = 170 if talk_line else 60
    title_max_h = 220
    title_f, title_lines, title_lh = fit_block(d, title_text, max_w, title_max_h, 60, 38, bold=True, line_height_ratio=1.25)
    y = draw_lines(d, (left, y), title_lines, title_f, hex_to_rgb(INK), title_lh)
    y += 28

    body_max_h = bottom - footer_zone - y - 20
    body_f, body_lines, body_lh = fit_block(d, axis.get("explanation", ""), max_w, max(body_max_h, 120), 34, 22, line_height_ratio=1.65)
    y = draw_lines(d, (left, y), body_lines, body_f, hex_to_rgb(INK), body_lh)

    if axis.get("confidence") == "low":
        y += 12
        d.text((left, y), "확실하지 않음 — 확인 필요", font=font(22, bold=False), fill=hex_to_rgb(INK_FAINT))

    if talk_line:
        box_top = bottom - 150
        d.rounded_rectangle([left, box_top, right, bottom - 62], radius=20, fill=hex_to_rgb("#eef2fa") + (255,))
        tx = left + 36
        if EMOJI_PATH:
            draw_emoji(img, "💬", (tx, box_top + 26), 32)
            tx += 46
        quote = f'"{talk_line}"'
        q_f, q_lines, q_lh = fit_block(d, quote, right - tx - 36, 100, 30, 20, bold=True, line_height_ratio=1.4)
        draw_lines(d, (tx, box_top + 26), q_lines[:2], q_f, hex_to_rgb(ACCENT), q_lh)

    draw_footer(img, d, page_no, total)
    return img.convert("RGB")


def draw_outro(data: dict, page_no: int, total: int) -> Image.Image:
    img, d, (left, top, right, bottom) = new_card()
    max_w = right - left

    y = top
    if EMOJI_PATH:
        draw_emoji(img, "🔎", (left, y), 40)
    d.text((left + 52, y), "더 파보고 싶으면", font=font(40, bold=True), fill=hex_to_rgb(INK))
    y += 92

    refs = data.get("references", []) or []
    if not refs:
        d.text((left, y), "없음", font=font(28, bold=False), fill=hex_to_rgb(INK_DIM))
    for ref in refs[:4]:
        line = f"{ref.get('source', '')} · {ref.get('title', '')}"
        f, lines, lh = fit_block(d, line, max_w, 120, 30, 22, line_height_ratio=1.5)
        y = draw_lines(d, (left, y), lines, f, hex_to_rgb(INK), lh) + 12

    cta_top = bottom - 320
    d.rounded_rectangle([left, cta_top, right, bottom - 76], radius=28, fill=hex_to_rgb(ACCENT) + (255,))
    cta_text = "매일 아침, 오늘 뉴스 하나 골라\n꺼드럭이 풀어드려요"
    cta_f = font(38, bold=True)
    cta_lines = cta_text.split("\n")
    cy = cta_top + 56
    for line in cta_lines:
        lw = d.textlength(line, font=cta_f)
        d.text(((W - lw) / 2, cy), line, font=cta_f, fill=(255, 255, 255))
        cy += 54
    sub = "팔로우 · 저장 · 공유"
    sub_f = font(26, bold=True)
    sw = d.textlength(sub, font=sub_f)
    d.text(((W - sw) / 2, cy + 20), sub, font=sub_f, fill=hex_to_rgb(ACCENT_TINT))

    draw_footer(img, d, page_no, total)
    return img.convert("RGB")


# ============================================================
# 오케스트레이션
# ============================================================

def render_cardnews(data: dict, out_dir: str | Path) -> list[Path]:
    """data(analyze_article()의 결과와 같은 구조)를 받아 카드뉴스 PNG들을 out_dir에 저장하고
    저장된 경로 목록을 순서대로 돌려준다."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    axes = data.get("axes", []) or []
    total = 2 + len(axes) + 1
    paths: list[Path] = []

    def save(img: Image.Image, name: str) -> None:
        p = out_dir / name
        img.save(p, "PNG")
        paths.append(p)

    save(draw_cover(data, 1, total), "01_표지.png")
    save(draw_summary(data, 2, total), "02_요약.png")
    for i, axis in enumerate(axes, start=1):
        family = axis.get("family", "축")
        save(draw_axis(axis, 2 + i, total), f"{2 + i:02d}_{family}.png")
    save(draw_outro(data, total, total), f"{total:02d}_마무리.png")

    return paths


# ============================================================
# Notion에서 실제 저장된 기사 불러오기 (notion_client.py의 저장 로직을 거꾸로 읽는다)
# ============================================================

_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")


def _extract_page_id(page_id_or_url: str) -> str:
    """Notion 페이지 URL이 들어와도, 순수 ID가 들어와도 32자리 ID로 정리해서 돌려준다."""
    m = re.search(r"([0-9a-fA-F]{32})", page_id_or_url.replace("-", ""))
    if not m:
        raise ValueError(f"Notion 페이지 ID를 이 값에서 찾지 못했어요: {page_id_or_url!r}")
    raw = m.group(1)
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _get_all_blocks(page_id: str, token: str) -> list[dict]:
    blocks = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        resp = requests.get(url, headers=_notion_headers(token), timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        blocks.extend(payload["results"])
        if not payload.get("has_more"):
            break
        cursor = payload["next_cursor"]
    return blocks


def _plain(rich_text: list[dict]) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


def _heading_text(block: dict) -> str | None:
    if block["type"] != "heading_2":
        return None
    return _plain(block["heading_2"]["rich_text"])


def _parse_callout_to_axis(block: dict) -> dict:
    runs = block["callout"]["rich_text"]
    icon = (block["callout"].get("icon") or {}).get("emoji", "")
    family = ICON_TO_FAMILY.get(icon, "기타")

    if not runs:
        return {"family": family, "title": "", "explanation": "", "sensitive": False, "confidence": "high"}

    label = runs[0].get("plain_text", "").rstrip("\n")
    warning_char = chr(0x26A0)  # WARNING SIGN
    variation_selector = chr(0xFE0F)  # VS16 (makes the warning sign render in color)
    sensitive_prefix = warning_char + variation_selector + " "
    sensitive = label.startswith(sensitive_prefix)
    title = label[len(sensitive_prefix):] if sensitive else label

    body_runs = runs[1:]
    # 뒤에서부터 talk_line, 저확신 표시를 떼어낸다 (notion_client._axis_callout이 이 순서로 붙였다)
    talk_line = None
    if body_runs and body_runs[-1].get("plain_text", "").startswith("\n\n\U0001f4ac "):
        talk_line = body_runs[-1]["plain_text"][len("\n\n\U0001f4ac ") :]
        body_runs = body_runs[:-1]

    confidence = "high"
    if body_runs and body_runs[-1].get("plain_text", "") == "\n(확실하지 않음 — 확인 필요)":
        confidence = "low"
        body_runs = body_runs[:-1]

    explanation_parts = []
    for run in body_runs:
        text = run.get("plain_text", "")
        bold = bool(run.get("annotations", {}).get("bold"))
        stripped = text.rstrip("\n")
        if bold and stripped:
            explanation_parts.append(f"**{stripped}**")
        else:
            explanation_parts.append(stripped)
    # 원본 encoding 쪽(_explanation_to_rich_text)이 줄마다 run을 하나씩 만들었으므로
    # (빈 줄도 포함해서) 여기서도 필터링 없이 그대로 이어 붙여야 원문과 정확히 일치한다.
    explanation = "\n".join(explanation_parts)

    return {
        "family": family,
        "title": title,
        "explanation": explanation,
        "sensitive": sensitive,
        "confidence": confidence,
        "talk_line": talk_line,
        "references": [],
    }


def _parse_reference_bullet(block: dict) -> dict:
    runs = block["bulleted_list_item"]["rich_text"]
    if not runs:
        return {"source": "", "title": "", "url": None}
    source = runs[0].get("plain_text", "").rstrip(" ·").strip()
    if len(runs) > 1:
        title_run = runs[1]
        title = title_run.get("plain_text", "")
        url = (title_run.get("text", {}) or {}).get("link", {})
        url = url.get("url") if url else None
    else:
        title, url = "", None
    return {"source": source, "title": title, "url": url}


def fetch_notion_article(page_id_or_url: str, notion_token: str) -> dict:
    """이미 Notion에 저장된 기사 페이지를 읽어서 render_cardnews()가 바로 쓸 수 있는
    data dict로 재구성한다. notion_client.py가 쓰는 형식을 그대로 거꾸로 읽는다."""
    if requests is None:
        raise RuntimeError("requests 패키지가 필요해요: pip install requests")

    page_id = _extract_page_id(page_id_or_url)
    resp = requests.get(f"https://api.notion.com/v1/pages/{page_id}", headers=_notion_headers(notion_token), timeout=30)
    resp.raise_for_status()
    props = resp.json()["properties"]

    title_runs = props.get("제목", {}).get("title", [])
    title = _plain(title_runs) or "(제목 없음)"
    category = (props.get("분야", {}).get("select") or {}).get("name", "")
    source_name_runs = props.get("출처명", {}).get("rich_text", [])
    source_name = _plain(source_name_runs)
    source_url = (props.get("출처 URL") or {}).get("url")
    published_date = (props.get("날짜", {}).get("date") or {}).get("start")

    blocks = _get_all_blocks(page_id, notion_token)

    summary: list[str] = []
    axes: list[dict] = []
    references: list[dict] = []
    section = None
    for block in blocks:
        heading = _heading_text(block)
        if heading is not None:
            if "요약" in heading:
                section = "summary"
            elif "포인트" in heading:
                section = "axes"
            elif "더 파보고" in heading:
                section = "references"
            else:
                section = None
            continue
        if section == "summary" and block["type"] == "bulleted_list_item":
            summary.append(_plain(block["bulleted_list_item"]["rich_text"]))
        elif section == "axes" and block["type"] == "callout":
            axes.append(_parse_callout_to_axis(block))
        elif section == "references" and block["type"] == "bulleted_list_item":
            references.append(_parse_reference_bullet(block))

    return {
        "title": title,
        "category": category,
        "source_name": source_name,
        "source_url": source_url,
        "published_date": published_date,
        "summary": summary,
        "axes": axes,
        "references": references,
    }


# ============================================================
# 예시 데이터 (--sample 용, 디자인 시안과 동일한 내용)
# ============================================================

SAMPLE_DATA = {
    "title": "한국은행, 기준금리 3.50%로 7연속 동결",
    "category": "시사경제",
    "source_name": "OO경제",
    "source_url": None,
    "published_date": "2026.09.07",
    "summary": [
        "한국은행이 기준금리를 3.50%로 유지하며 7연속 동결을 결정했다.",
        "물가는 안정세지만, 가계부채와 환율 부담이 여전히 변수로 꼽힌다.",
        "하반기 인하 가능성에는 총재도 신중한 입장을 유지했다.",
    ],
    "axes": [
        {
            "family": "용어 뽀개기",
            "title": "기준금리가 뭐길래",
            "explanation": "기준금리는 한국은행이 시중은행에 돈을 빌려줄 때 적용하는 기본 이자율이에요. 이 숫자 하나가 은행 예금·대출 금리는 물론, 부동산과 주식 같은 자산 시장까지 돈이 흘러가는 방향 전체에 영향을 줘요.",
            "talk_line": "기준금리가 오르면 대출 이자도 같이 뛴다고 보면 돼",
            "sensitive": False,
            "confidence": "high",
        },
        {
            "family": "원리 뽀개기",
            "title": "왜 안 올리고 안 내릴까",
            "explanation": "금리를 올리면 물가는 잡히지만 대출자 부담과 경기 둔화가 커지고, 내리면 반대로 물가와 자산시장 과열 우려가 생겨요. 지금은 물가·경기·가계부채가 팽팽히 맞서 있어서, 한국은행은 일단 '동결'을 가장 무난한 선택으로 보고 있어요.",
            "talk_line": "지금은 올려도 내려도 리스크가 커서, 일단 지켜보자는 거야",
            "sensitive": False,
            "confidence": "high",
        },
        {
            "family": "과거 썰",
            "title": "여기까지 오는 흐름",
            "explanation": "한국은행은 물가가 가파르게 오르던 시기에 여러 차례 금리를 끌어올렸다가, 물가가 안정되기 시작한 뒤로는 계속 동결 기조를 이어오고 있어요. 이번 결정으로 벌써 7연속 동결이에요.",
            "talk_line": "한동안 계속 그대로였다가, 이번에도 그대로인 거야",
            "sensitive": False,
            "confidence": "high",
        },
        {
            "family": "싸움의 이유",
            "title": "그래서 누가 뭘 걱정하는데",
            "explanation": "인하를 원하는 쪽은 대출자·자영업자의 이자 부담 완화를 근거로 들어요. 반대로 신중론 쪽은 가계부채가 더 늘어날 위험과 환율 불안을 근거로 들죠. 둘 다 틀린 말은 아니라서, 한국은행은 계속 저울질하고 있어요.",
            "talk_line": "내리자는 쪽은 '살림살이', 신중한 쪽은 '빚 폭탄'을 걱정하는 거야",
            "sensitive": False,
            "confidence": "high",
        },
    ],
    "references": [
        {"source": "한국은행", "title": "통화정책방향 결정문 (2026.09)", "url": None},
        {"source": "OO경제", "title": "관련 기사 원문 보기", "url": None},
    ],
}


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="꺼드럭 분석 결과를 카드뉴스 PNG로 만든다.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--notion-page-id", help="이미 저장된 Notion 페이지 URL 또는 ID")
    src.add_argument("--json", help="analyze_article() 결과를 저장해둔 JSON 파일 경로")
    src.add_argument("--sample", action="store_true", help="내장 예시 데이터로 테스트 렌더링")
    parser.add_argument("--out", default="cardnews_out", help="결과 PNG를 저장할 폴더 (기본: cardnews_out)")
    parser.add_argument("--notion-token", help="Notion 토큰 (미지정 시 NOTION_TOKEN 환경변수 사용)")
    args = parser.parse_args()

    if args.sample:
        data = SAMPLE_DATA
    elif args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    else:
        import os

        token = args.notion_token or os.environ.get("NOTION_TOKEN")
        if not token:
            print("Notion 토큰이 필요해요 — --notion-token 으로 주거나 NOTION_TOKEN 환경변수를 설정해주세요.", file=sys.stderr)
            sys.exit(1)
        data = fetch_notion_article(args.notion_page_id, token)

    paths = render_cardnews(data, args.out)
    print(f"{len(paths)}장 저장했어요 -> {Path(args.out).resolve()}")
    for p in paths:
        print(" -", p.name)


if __name__ == "__main__":
    main()
