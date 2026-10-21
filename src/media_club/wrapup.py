from __future__ import annotations

import io
from collections.abc import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from media_club.media_types import MEDIA_TYPES, MediaKind

WIDTH = 1080
HEIGHT = 1350
BACKGROUND = "#1f1f20"
PANEL = "#29292b"
ACCENT = "#ef4b63"
TEXT = "#f4f0ef"
MUTED = "#b9b3b3"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(filename, size)
    except OSError:
        return ImageFont.load_default(size=size)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    max_width: int,
    start_size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = start_size
    while size > 22:
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(22, bold=bold)


def _draw_avatar(
    canvas: Image.Image,
    avatar_data: bytes | None,
    box: tuple[int, int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(box, fill=PANEL, outline=ACCENT, width=7)
    if avatar_data is None:
        return
    try:
        avatar = Image.open(io.BytesIO(avatar_data)).convert("RGB")
    except (OSError, UnidentifiedImageError):
        return
    size = box[2] - box[0] - 16
    avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    canvas.paste(avatar, (box[0] + 8, box[1] + 8), mask)


def render_wrapup(
    display_name: str,
    season_year: int,
    counts: Mapping[MediaKind, int],
    avatar_data: bytes | None = None,
) -> bytes:
    """Render a shareable Ghoulstober season summary as a PNG."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((30, 30, WIDTH - 30, HEIGHT - 30), 42, outline=ACCENT, width=5)
    for offset in range(-500, 1300, 140):
        draw.line((0, offset, 420, offset + 420), fill="#272729", width=2)

    eyebrow = _font(28, bold=True)
    draw.text((72, 72), "GHOULSTOBER", font=eyebrow, fill=ACCENT)
    title = _font(106, bold=True)
    draw.text(
        (68, 112),
        "WRAP-UP",
        font=title,
        fill=BACKGROUND,
        stroke_width=4,
        stroke_fill=ACCENT,
    )
    year_font = _font(44, bold=True)
    draw.text((74, 245), f"{season_year} SEASON", font=year_font, fill=TEXT)

    _draw_avatar(canvas, avatar_data, (820, 68, 1000, 248))
    name_font = _fit_text(draw, display_name, 650, 40, bold=True)
    draw.text((74, 315), display_name, font=name_font, fill=TEXT)
    draw.text((74, 367), "YOUR HAUNTED MEDIA LEDGER", font=eyebrow, fill=MUTED)

    total = sum(counts.values())
    draw.rounded_rectangle((70, 430, 1010, 615), 28, fill=PANEL, outline=ACCENT, width=3)
    total_font = _font(104, bold=True)
    draw.text((112, 447), str(total), font=total_font, fill=ACCENT)
    draw.text((315, 463), "TOTAL", font=_font(31, bold=True), fill=MUTED)
    draw.text((315, 505), "THINGS LOGGED", font=_font(48, bold=True), fill=TEXT)

    card_width = 450
    card_height = 128
    start_y = 664
    gap_x = 40
    gap_y = 24
    for index, (kind, definition) in enumerate(MEDIA_TYPES.items()):
        column = index % 2
        row = index // 2
        left = 70 + column * (card_width + gap_x)
        top = start_y + row * (card_height + gap_y)
        right = left + card_width
        bottom = top + card_height
        draw.rounded_rectangle((left, top, right, bottom), 22, fill=PANEL)
        draw.line((left, top + 22, left, bottom - 22), fill=ACCENT, width=5)
        count = counts.get(kind, 0)
        draw.text((left + 28, top + 18), str(count), font=_font(62, bold=True), fill=ACCENT)
        label_font = _fit_text(draw, definition.tag_name.upper(), 290, 28, bold=True)
        draw.text((left + 135, top + 47), definition.tag_name.upper(), font=label_font, fill=TEXT)

    footer_y = 1280
    draw.line((72, footer_y - 24, 1008, footer_y - 24), fill="#414145", width=2)
    draw.text(
        (72, footer_y),
        "VIEWING PERIOD  |  SEP 28 - OCT 31",
        font=_font(22, bold=True),
        fill=MUTED,
    )
    footer = "LOGGED WITH GHOUL BOT"
    footer_font = _font(22, bold=True)
    footer_width = draw.textbbox((0, 0), footer, font=footer_font)[2]
    draw.text((1008 - footer_width, footer_y), footer, font=footer_font, fill=ACCENT)

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()
