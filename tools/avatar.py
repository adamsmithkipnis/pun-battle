"""Draw the Bluesky avatar: a winking alarm clock with both hands on 12.

    .venv/bin/pip install Pillow        # tool-only; the bot doesn't need it
    .venv/bin/python tools/avatar.py    # writes assets/avatar.png + preview

Both hands on 12 because a theme only ever goes up on the hour; the wink
because it's a pun account. Bluesky crops avatars to a circle and shows them
at ~40px in the feed, so everything sits well inside the circle and the
shapes are big and flat — assets/avatar-preview.png shows it round at feed
size, which is the real test.

Drawn at 2x and downsampled, which is Pillow's only route to smooth edges.
"""

import argparse
import math
import os

from PIL import Image, ImageDraw

SIZE = 1000
S = SIZE * 2                       # supersampled canvas

BG = (255, 196, 61)                # warm yellow
BODY = (59, 42, 122)               # deep purple
DIAL = (255, 247, 228)             # cream
CHEEK = (255, 138, 128)
INK = BODY


def _polar(cx, cy, r, degrees):
    """Clock angles: 0 = 12 o'clock, clockwise."""
    rad = math.radians(degrees - 90)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _thick_line(draw, a, b, width, fill):
    draw.line([a, b], fill=fill, width=width)
    for x, y in (a, b):            # round caps
        draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2],
                     fill=fill)


def draw_avatar() -> Image.Image:
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    cx, cy = S / 2, S * 0.54
    body_r, dial_r = S * 0.30, S * 0.25

    # Feet, behind the body.
    for angle in (145, 215):
        fx, fy = _polar(cx, cy, body_r * 1.02, angle)
        _thick_line(d, _polar(cx, cy, body_r * 0.7, angle), (fx, fy),
                    int(S * 0.045), BODY)

    # Bells: domes tilted outward, with a striker bar across the top.
    for angle in (-40, 40):
        bx, by = _polar(cx, cy, body_r * 1.08, angle)
        r = S * 0.105
        bell = Image.new("RGBA", (int(r * 2), int(r * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(bell).pieslice([0, 0, r * 2, r * 2], 180, 360, fill=BODY)
        ImageDraw.Draw(bell).rectangle([0, r - S * 0.012, r * 2, r + S * 0.012],
                                       fill=BODY)
        bell = bell.rotate(-angle, resample=Image.BICUBIC, expand=True)
        img.paste(bell, (int(bx - bell.width / 2), int(by - bell.height / 2)), bell)
    top = _polar(cx, cy, body_r * 1.16, 0)
    _thick_line(d, (top[0], top[1] + S * 0.02), (top[0], cy - body_r),
                int(S * 0.04), BODY)

    # Body and dial.
    d.ellipse([cx - body_r, cy - body_r, cx + body_r, cy + body_r], fill=BODY)
    d.ellipse([cx - dial_r, cy - dial_r, cx + dial_r, cy + dial_r], fill=DIAL)

    # No hour marks: at 3 and 9 they read as eyebrows beside the eyes, and
    # at feed size every extra stroke is noise.

    # Face: one open eye, one wink, rosy cheeks, a wide grin.
    eye_y, eye_dx = cy + dial_r * 0.02, dial_r * 0.42
    er = S * 0.034
    d.ellipse([cx - eye_dx - er, eye_y - er * 1.2, cx - eye_dx + er, eye_y + er * 1.2],
              fill=INK)
    wink = [cx + eye_dx - er * 1.3, eye_y - er * 1.1, cx + eye_dx + er * 1.3, eye_y + er * 1.1]
    d.arc(wink, 200, 340, fill=INK, width=int(S * 0.026))
    for sx in (-1, 1):
        chx, chy = cx + sx * dial_r * 0.62, cy + dial_r * 0.30
        d.ellipse([chx - S * 0.04, chy - S * 0.022, chx + S * 0.04, chy + S * 0.022],
                  fill=CHEEK)
    smile = [cx - dial_r * 0.40, cy + dial_r * 0.05, cx + dial_r * 0.40, cy + dial_r * 0.62]
    d.chord(smile, 10, 170, fill=INK)

    # Hands, both on 12, drawn last so they sit on top like a real clock's.
    pivot = (cx, cy - dial_r * 0.12)
    _thick_line(d, pivot, (cx, cy - dial_r * 0.78), int(S * 0.020), INK)   # minute
    _thick_line(d, pivot, (cx, cy - dial_r * 0.55), int(S * 0.038), INK)   # hour
    d.ellipse([pivot[0] - S * 0.028, pivot[1] - S * 0.028,
               pivot[0] + S * 0.028, pivot[1] + S * 0.028], fill=INK)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def preview(avatar: Image.Image) -> Image.Image:
    """The avatar as Bluesky shows it: round, at profile and feed sizes."""
    sheet = Image.new("RGB", (620, 220), (255, 255, 255))
    x = 10
    for size in (200, 96, 48, 32):
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size * 4, size * 4], fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)
        small = avatar.resize((size, size), Image.LANCZOS)
        sheet.paste(small, (x, (220 - size) // 2), mask)
        x += size + 30
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"))
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    avatar = draw_avatar()
    avatar.save(os.path.join(args.out, "avatar.png"), optimize=True)
    preview(avatar).save(os.path.join(args.out, "avatar-preview.png"))
    print(f"wrote {args.out}/avatar.png and avatar-preview.png")


if __name__ == "__main__":
    main()
