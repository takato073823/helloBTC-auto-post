"""Generate deterministic editorial crypto images without paid image APIs."""

from __future__ import annotations

import hashlib
import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient(width: int, height: int, rng: random.Random) -> Image.Image:
    top = (6 + rng.randrange(8), 12 + rng.randrange(12), 28 + rng.randrange(16))
    bottom = (10 + rng.randrange(14), 22 + rng.randrange(18), 42 + rng.randrange(22))
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def _glow(image: Image.Image, x: int, y: int, radius: int, color: tuple[int, int, int]) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, 115))
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 2))
    image.paste(layer, (0, 0), layer)


def _draw_market_chart(draw: ImageDraw.ImageDraw, width: int, height: int, rng: random.Random) -> None:
    left, top = int(width * 0.08), int(height * 0.18)
    right, bottom = int(width * 0.92), int(height * 0.82)
    grid = (72, 94, 126)
    for i in range(1, 6):
        y = top + (bottom - top) * i // 6
        draw.line((left, y, right, y), fill=grid, width=1)
    for i in range(1, 9):
        x = left + (right - left) * i // 9
        draw.line((x, top, x, bottom), fill=grid, width=1)

    count = 22
    candle_w = max(7, (right - left) // (count * 2))
    price = (top + bottom) // 2
    points: list[tuple[int, int]] = []
    for i in range(count):
        x = left + (right - left) * i // (count - 1)
        opening = price + rng.randint(-30, 30)
        closing = opening + rng.randint(-60, 60)
        high = min(opening, closing) - rng.randint(12, 45)
        low = max(opening, closing) + rng.randint(12, 45)
        opening = max(top + 40, min(bottom - 40, opening))
        closing = max(top + 40, min(bottom - 40, closing))
        high = max(top + 15, min(opening, closing, high))
        low = min(bottom - 15, max(opening, closing, low))
        color = (35, 201, 151) if closing < opening else (242, 91, 91)
        draw.line((x, high, x, low), fill=color, width=3)
        draw.rounded_rectangle(
            (x - candle_w, min(opening, closing), x + candle_w, max(opening, closing)),
            radius=2,
            fill=color,
        )
        price = closing
        points.append((x, closing))
    draw.line(points, fill=(247, 147, 26), width=5, joint="curve")


def _draw_network(draw: ImageDraw.ImageDraw, width: int, height: int, rng: random.Random) -> None:
    nodes = [
        (rng.randint(int(width * 0.08), int(width * 0.92)), rng.randint(int(height * 0.14), int(height * 0.86)))
        for _ in range(18)
    ]
    for i, (x1, y1) in enumerate(nodes):
        nearest = sorted(nodes[i + 1 :], key=lambda p: (p[0] - x1) ** 2 + (p[1] - y1) ** 2)[:2]
        for x2, y2 in nearest:
            draw.line((x1, y1, x2, y2), fill=(59, 113, 164), width=2)
    for i, (x, y) in enumerate(nodes):
        radius = 8 + (i % 4) * 2
        color = (247, 147, 26) if i % 5 == 0 else (61, 176, 255)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def create_editorial_image(
    seed_text: str,
    *,
    width: int = 1200,
    height: int = 630,
) -> bytes:
    """Return a reusable JPEG generated entirely on the GitHub runner."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    image = _gradient(width, height, rng).convert("RGBA")

    _glow(image, int(width * 0.22), int(height * 0.25), int(height * 0.28), (247, 147, 26))
    _glow(image, int(width * 0.78), int(height * 0.70), int(height * 0.33), (35, 119, 255))

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    prompt = seed_text.lower()
    if any(word in prompt for word in ("chart", "trading", "market", "price", "bitcoin", "btc")):
        _draw_market_chart(draw, width, height, rng)
    else:
        _draw_network(draw, width, height, rng)

    # Editorial framing only. テキストを置かないため、画像品質検査の代替としても使える。
    draw.rounded_rectangle(
        (int(width * 0.035), int(height * 0.055), int(width * 0.965), int(height * 0.945)),
        radius=28,
        outline=(255, 255, 255, 50),
        width=2,
    )
    image.alpha_composite(overlay)
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()
