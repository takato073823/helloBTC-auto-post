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


def _draw_security_incident(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """ガバナンス攻撃・流出など、セキュリティ事案向けの文字なし代替。"""
    center_x, center_y = width // 2, height // 2
    size = min(width, height) // 4
    shield = [
        (center_x, center_y - size),
        (center_x + size, center_y - size // 2),
        (center_x + int(size * 0.72), center_y + int(size * 0.72)),
        (center_x, center_y + size),
        (center_x - int(size * 0.72), center_y + int(size * 0.72)),
        (center_x - size, center_y - size // 2),
    ]
    draw.polygon(shield, fill=(22, 54, 88), outline=(77, 190, 255), width=7)
    draw.line(
        [(center_x - size // 3, center_y - size // 2), (center_x + size // 8, center_y - size // 8),
         (center_x - size // 8, center_y + size // 5), (center_x + size // 3, center_y + size // 2)],
        fill=(245, 84, 84), width=9,
    )
    draw.rounded_rectangle(
        (center_x - size // 5, center_y - size // 12, center_x + size // 5, center_y + size // 3),
        radius=20, fill=(9, 19, 36), outline=(132, 177, 219), width=4,
    )
    draw.ellipse((center_x - size // 12, center_y, center_x + size // 12, center_y + size // 6), fill=(245, 84, 84))


def _draw_etf_flow(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """ETFの資金流入・流出を、価格チャートなしで表す。"""
    left_x, right_x = int(width * 0.27), int(width * 0.73)
    center_y = int(height * 0.56)
    radius = int(min(width, height) * 0.16)
    for x, color in ((left_x, (247, 176, 52)), (right_x, (125, 190, 242))):
        draw.ellipse((x - radius, center_y - radius, x + radius, center_y + radius),
                     fill=(17, 37, 62), outline=color, width=8)
        draw.ellipse((x - radius + 18, center_y - radius + 18, x + radius - 18, center_y + radius - 18),
                     outline=(230, 240, 250), width=3)
    for offset in (-70, 0, 70):
        draw.line((int(width * 0.08), center_y + offset, int(width * 0.44), center_y + offset),
                  fill=(90, 209, 198), width=10)
        draw.polygon([(int(width * 0.44), center_y + offset), (int(width * 0.40), center_y + offset - 18),
                      (int(width * 0.40), center_y + offset + 18)], fill=(90, 209, 198))
        draw.line((int(width * 0.92), center_y + offset, int(width * 0.56), center_y + offset),
                  fill=(90, 209, 198), width=10)
        draw.polygon([(int(width * 0.56), center_y + offset), (int(width * 0.60), center_y + offset - 18),
                      (int(width * 0.60), center_y + offset + 18)], fill=(90, 209, 198))


def _draw_regulatory_filing(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """規制当局への提出・修正を、無地の書類と保管トレイで表す。"""
    left, top = int(width * 0.20), int(height * 0.18)
    page_w, page_h = int(width * 0.42), int(height * 0.58)
    for offset, fill in ((32, (57, 72, 90)), (16, (76, 92, 111)), (0, (208, 217, 226))):
        draw.rounded_rectangle(
            (left + offset, top + offset, left + page_w + offset, top + page_h + offset),
            radius=16, fill=fill, outline=(235, 240, 245), width=3,
        )
    seal_x, seal_y = int(width * 0.74), int(height * 0.50)
    radius = int(min(width, height) * 0.18)
    draw.ellipse((seal_x - radius, seal_y - radius, seal_x + radius, seal_y + radius),
                 fill=(34, 50, 69), outline=(171, 187, 204), width=8)
    draw.ellipse((seal_x - radius + 22, seal_y - radius + 22, seal_x + radius - 22, seal_y + radius - 22),
                 outline=(104, 139, 173), width=4)


def _is_explicit_market_chart_request(prompt: str) -> bool:
    """価格チャートを明示した記事だけに限定し、銘柄名だけで発火させない。"""
    text = prompt.lower()
    return any(phrase in text for phrase in (
        "candlestick chart", "price chart", "market chart", "trading chart", "ローソク足", "価格チャート",
    ))


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
    if any(word in prompt for word in ("governance", "exploit", "attack", "hack", "breach", "security", "流出", "攻撃", "脆弱性")):
        _draw_security_incident(draw, width, height)
    elif any(word in prompt for word in ("sec filing", "regulatory filing", "amendment", "提出書類", "修正書類", "規制")):
        _draw_regulatory_filing(draw, width, height)
    elif any(word in prompt for word in ("etf", "inflow", "outflow", "fund flow", "資金流入", "資金流出")):
        _draw_etf_flow(draw, width, height)
    elif _is_explicit_market_chart_request(prompt):
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
