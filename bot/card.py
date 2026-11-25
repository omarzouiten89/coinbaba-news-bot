"""Draws the COINBABA "lesson of the day" card: dark navy + gold, your photo, the logo and the title."""
from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZE = 1080
NAVY_TOP = (12, 22, 48)
NAVY_BOTTOM = (4, 8, 20)
GOLD = (222, 184, 96)
WHITE = (245, 245, 250)

# First font found is used: a bundled font, the Ubuntu font on GitHub, then the Mac system font.
FONT_CANDIDATES = [
    ASSETS / "fonts" / "NotoKufiArabic-Bold.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
    Path("/System/Library/Fonts/SFArabic.ttf"),
    Path("/System/Library/Fonts/GeezaPro.ttc"),
]


LATIN_FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
    Path("/System/Library/Fonts/SFNS.ttf"),
]


def _latin_font(size: int) -> ImageFont.FreeTypeFont:
    for path in LATIN_FONT_CANDIDATES:
        if path.exists():
            if path.suffix == ".ttc":  # Helvetica.ttc: face 1 is Bold
                try:
                    return ImageFont.truetype(str(path), size, index=1)
                except OSError:
                    pass
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def card_title(text: str) -> str:
    """The card shows Arabic only: '(Spot)' style Latin parts are removed (they stay in the post text)."""
    import re
    text = re.sub(r"\(\s*[A-Za-z0-9 .&/+-]+\s*\)", "", text)
    text = re.sub(r"[A-Za-z]+", "", text)
    return " ".join(text.split()).replace(" ؟", "؟")


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.exists():
            font = ImageFont.truetype(str(path), size)
            try:  # variable fonts (Mac): pick the bold style
                font.set_variation_by_name("Bold")
            except Exception:
                pass
            return font
    raise RuntimeError("No Arabic font found")


def _ar(text: str) -> str:
    """Arabic letters joined and ordered right-to-left for drawing."""
    return get_display(arabic_reshaper.reshape(text))


def _runs(visual: str) -> List[tuple]:
    """Splits a visual-order line into (is_digit_run, text) pieces: digits use the Latin font."""
    import re
    return [(bool(re.fullmatch(r"[0-9.,%$:+\-]+", part)), part)
            for part in re.split(r"([0-9][0-9.,%$:+\-]*)", visual) if part]


def _mixed_width(draw: ImageDraw.ImageDraw, visual: str, ar_font, latin_font) -> float:
    return sum(draw.textlength(t, font=latin_font if d else ar_font) for d, t in _runs(visual))


def _draw_mixed_right(draw: ImageDraw.ImageDraw, right: int, y: int, visual: str, ar_font, latin_font, fill) -> None:
    """Draws a line ending at `right`, Arabic glyphs with the Arabic font and 0-9 with the Latin font."""
    x = right - _mixed_width(draw, visual, ar_font, latin_font)
    baseline = y + ar_font.getmetrics()[0]  # both fonts sit on the same baseline
    for is_digit, text in _runs(visual):
        font = latin_font if is_digit else ar_font
        draw.text((x, baseline), text, font=font, fill=fill, anchor="ls")
        x += draw.textlength(text, font=font)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw,
          latin_font=None) -> List[str]:
    latin_font = latin_font or font
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if _mixed_width(draw, _ar(trial), font, latin_font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _background() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE))
    px = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / SIZE
        px.line([(0, y), (SIZE, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOTTOM)))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((560, -260, 1340, 520), fill=(*GOLD, 38))
    img.paste(glow.filter(ImageFilter.GaussianBlur(120)), (0, 0), glow.filter(ImageFilter.GaussianBlur(120)))
    return img


def _photo_layer(height: int) -> Optional[Image.Image]:
    path = ASSETS / "photo.jpg"
    if not path.exists():
        return None
    photo = Image.open(path).convert("RGB")
    width = int(photo.width * height / photo.height)
    photo = photo.resize((width, height), Image.LANCZOS)
    # Soft fade on the right side and the top so the photo melts into the background.
    mask = Image.new("L", photo.size, 255)
    draw = ImageDraw.Draw(mask)
    fade = int(width * 0.35)
    for x in range(fade):
        draw.line([(width - fade + x, 0), (width - fade + x, height)], fill=int(255 * (1 - x / fade)))
    top_fade = int(height * 0.18)
    top = Image.new("L", photo.size, 255)
    tdraw = ImageDraw.Draw(top)
    for y in range(top_fade):
        tdraw.line([(0, y), (width, y)], fill=int(255 * y / top_fade))
    mask = Image.composite(mask, Image.new("L", photo.size, 0), top)
    layer = Image.new("RGBA", photo.size)
    layer.paste(photo, (0, 0))
    layer.putalpha(mask)
    return layer


def render_card(title: str, badge: str = "معلومة اليوم", handle: str = "@financemante",
                subtitle: str = "") -> bytes:
    img = _background()
    draw = ImageDraw.Draw(img)

    photo = _photo_layer(760)
    if photo is not None:
        img.paste(photo, (-40, SIZE - photo.height), photo)

    # Logo (top left)
    mark_path, word_path = ASSETS / "logo-mark.png", ASSETS / "logo-wordmark.png"
    if mark_path.exists():
        mark = Image.open(mark_path).convert("RGBA").resize((96, 96), Image.LANCZOS)
        img.paste(mark, (48, 44), mark)
    if word_path.exists():
        word = Image.open(word_path).convert("RGBA")
        word = word.resize((230, int(word.height * 230 / word.width)), Image.LANCZOS)
        img.paste(word, (156, 44 + (96 - word.height) // 2), word)

    # Badge (top right)
    badge_font = _font(36)
    badge_text = _ar(badge)
    bw = draw.textlength(badge_text, font=badge_font)
    x1 = SIZE - 48
    draw.rounded_rectangle((x1 - bw - 56, 52, x1, 52 + 76), radius=38, fill=GOLD)
    draw.text((x1 - 28, 52 + 38), badge_text, font=badge_font, fill=NAVY_BOTTOM, anchor="rm")

    # Title (right side, right aligned)
    title = card_title(title)
    area_right, area_width = SIZE - 64, 560
    size = 76
    while True:
        title_font = _font(size)
        digit_font = _latin_font(int(size * 0.92))
        lines = _wrap(title, title_font, area_width, draw, digit_font)
        if len(lines) <= 4 or size <= 48:
            break
        size -= 6
    line_h = int(size * 1.45)
    sub_size = 44
    sub_h = int(sub_size * 1.6) if subtitle else 0
    block_h = line_h * len(lines) + sub_h
    y = 470 - block_h // 2
    for line in lines:
        _draw_mixed_right(draw, area_right, y, _ar(line), title_font, digit_font, WHITE)
        y += line_h
    if subtitle:  # e.g. the date of the morning brief: smaller and gold
        _draw_mixed_right(draw, area_right, y, _ar(card_title(subtitle)), _font(sub_size),
                          _latin_font(int(sub_size * 0.92)), GOLD)
        y += sub_h
    draw.rounded_rectangle((area_right - 150, y + 18, area_right, y + 26), radius=4, fill=GOLD)

    # Handle (bottom right)
    draw.text((SIZE - 64, SIZE - 70), handle, font=_latin_font(34), fill=GOLD, anchor="rs")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


if __name__ == "__main__":
    import sys
    Path(sys.argv[2] if len(sys.argv) > 2 else "card_preview.jpg").write_bytes(
        render_card(sys.argv[1] if len(sys.argv) > 1 else "كيف تحدد مستويات الدعم والمقاومة؟"))
