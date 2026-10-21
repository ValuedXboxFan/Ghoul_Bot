import io

from PIL import Image, ImageColor

from media_club.wrapup import ACCENT, HEIGHT, WIDTH, render_wrapup


def test_wrapup_renders_category_totals_as_png() -> None:
    result = render_wrapup(
        "Ghoul Friend",
        2026,
        {
            "movie": 14,
            "tv_show": 3,
            "book": 2,
            "video_game": 1,
        },
    )

    image = Image.open(io.BytesIO(result))
    assert image.format == "PNG"
    assert image.size == (WIDTH, HEIGHT)
    assert image.getpixel((30, 100)) == ImageColor.getrgb(ACCENT)
