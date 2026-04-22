"""Generate the GitHub social-preview image.

Renders a synthetic `allocator show` table as a PNG using Pillow + SF Mono,
sized 1280x640 for GitHub's social-preview slot. All data is fabricated to
match the style of the README's example output — never run this against a
real portfolio.

Usage:
    uv run python scripts/generate_social_preview.py

Writes `docs/social-preview.png`. Pillow is not a project dependency; install
locally with `uv pip install pillow` before running.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 640
SAFE = 40  # GitHub recommends keeping important content ≥40pt from each edge.

# Catppuccin Mocha — tuned for contrast against a near-black background.
BG = (13, 17, 23)  # GitHub dark bg
BG_PANEL = (20, 24, 30)  # subtle panel card
TEXT = (205, 214, 244)  # primary text
DIM = (127, 132, 156)  # secondary / gridlines
ACCENT = (203, 166, 247)  # title / branding
GREEN = (166, 227, 161)  # positive / target
YELLOW = (249, 226, 175)  # overweight drift
BLUE = (137, 180, 250)  # underweight drift
ORANGE = (250, 179, 135)  # currency accent

FONT_PATH = "/System/Library/Fonts/SFNSMono.ttf"
FONT_FALLBACK = "/System/Library/Fonts/Menlo.ttc"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_PATH if Path(FONT_PATH).exists() else FONT_FALLBACK
    try:
        # SFNSMono.ttf doesn't expose weight via name — use whichever works.
        return ImageFont.truetype(path, size=size, index=1 if bold else 0)
    except (OSError, IndexError):
        return ImageFont.truetype(path, size=size)


# ─────────────────────────── data (synthetic) ───────────────────────────
# Hand-constructed $100,000 sample portfolio — nothing here matches any real
# user's holdings. The round-number drift and clean percentages make it easy
# to eyeball that the tool math is working correctly.
ROWS: list[tuple[str, str, str, str, str, str, str]] = [
    # Category, Symbol, Current, Curr%, Tgt%, Drift, Δ$
    ("Alternatives", "VNQ", "$4,700.00", "4.70%", "5.00%", "-0.30%", "-$300.00"),
    ("Bonds", "BND", "$2,850.00", "2.85%", "3.00%", "-0.15%", "-$150.00"),
    ("", "BNDX", "$1,050.00", "1.05%", "1.00%", "+0.05%", "+$50.00"),
    ("Cash", "VMFXX", "$1,100.00", "1.10%", "1.00%", "+0.10%", "+$100.00"),
    ("Intl Stocks", "VEA", "$16,100.00", "16.10%", "15.00%", "+1.10%", "+$1,100.00"),
    ("", "VWO", "$9,500.00", "9.50%", "10.00%", "-0.50%", "-$500.00"),
    ("US Stocks", "VTI", "$41,500.00", "41.50%", "40.00%", "+1.50%", "+$1,500.00"),
    ("", "VB", "$23,200.00", "23.20%", "25.00%", "-1.80%", "-$1,800.00"),
]
TOTAL_VALUE_TEXT = "$100,000.00"

COLS = ["Category", "Symbol", "Current", "Curr %", "Tgt %", "Drift", "Δ $"]
COL_WIDTHS = [180, 90, 140, 100, 100, 120, 140]
COL_ALIGN = ["left", "left", "right", "right", "right", "right", "right"]


def _draw_cell(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    align: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    if align == "right":
        x = x + width - text_w - 8
    elif align == "left":
        x = x + 8
    draw.text((x, y), text, font=font, fill=color)


def _drift_color(drift: str) -> tuple[int, int, int]:
    if drift.startswith("+"):
        return YELLOW
    if drift.startswith("-") and drift != "-":
        return BLUE
    return TEXT


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    title_f = _font(44, bold=True)
    subtitle_f = _font(22)
    header_f = _font(22, bold=True)
    body_f = _font(22)
    tag_f = _font(18)

    # All important content lives inside the safe box:
    # (SAFE, SAFE) → (WIDTH - SAFE, HEIGHT - SAFE) = (40, 40) → (1240, 600).

    # ───── header ─────
    draw.text((SAFE, SAFE + 4), "allocator", font=title_f, fill=ACCENT)
    draw.text(
        (SAFE, SAFE + 64),
        "Personal portfolio rebalancer · withdrawal planner · MPT optimizer",
        font=subtitle_f,
        fill=TEXT,
    )
    draw.text(
        (SAFE, SAFE + 94),
        "python · cli · monarch · yfinance · coingecko",
        font=tag_f,
        fill=DIM,
    )

    # ───── panel ─────
    panel_x = SAFE
    panel_y = SAFE + 140
    panel_w = WIDTH - 2 * SAFE
    panel_h = HEIGHT - SAFE - panel_y  # bottom edge lands at (HEIGHT - SAFE)
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
        radius=14,
        fill=BG_PANEL,
    )

    # Panel title bar
    draw.text(
        (panel_x + 24, panel_y + 18),
        "IRA — allocation",
        font=header_f,
        fill=TEXT,
    )
    total_value_text = TOTAL_VALUE_TEXT
    total_value_w = draw.textlength(total_value_text, font=header_f)
    draw.text(
        (panel_x + panel_w - 24 - total_value_w, panel_y + 18),
        total_value_text,
        font=header_f,
        fill=ORANGE,
    )
    total_label_text = "Total value"
    total_label_w = draw.textlength(total_label_text, font=tag_f)
    draw.text(
        (panel_x + panel_w - 24 - total_value_w - total_label_w - 12, panel_y + 22),
        total_label_text,
        font=tag_f,
        fill=DIM,
    )

    # Header row
    row_y = panel_y + 72
    col_x = panel_x + 24
    total_table_w = sum(COL_WIDTHS)
    available_w = panel_w - 48
    scale = available_w / total_table_w
    scaled_widths = [int(w * scale) for w in COL_WIDTHS]

    running_x = col_x
    for i, header in enumerate(COLS):
        _draw_cell(
            draw,
            header,
            running_x,
            row_y,
            scaled_widths[i],
            header_f,
            DIM,
            COL_ALIGN[i],
        )
        running_x += scaled_widths[i]

    # Separator
    draw.line(
        (col_x, row_y + 34, col_x + available_w, row_y + 34),
        fill=DIM,
        width=1,
    )

    # Body rows
    body_y = row_y + 44
    row_height = 32
    for r, row in enumerate(ROWS):
        running_x = col_x
        drift = row[5]
        accent = _drift_color(drift)
        for i, cell in enumerate(row):
            color = TEXT
            if i == 5 or i == 6:
                color = accent
            _draw_cell(
                draw,
                cell,
                running_x,
                body_y + r * row_height,
                scaled_widths[i],
                body_f,
                color,
                COL_ALIGN[i],
            )
            running_x += scaled_widths[i]

    out = Path("docs/social-preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
