"""アイキャッチ画像の縦横比を保った仕上げ処理。"""

import io

from PIL import Image, ImageOps


SAFE_COMPOSITION_PROMPT = (
    "All objects must keep natural, geometrically accurate proportions with no stretching, compression, or warping. "
    "Use a balanced editorial composition: keep every primary subject fully inside the frame with at least "
    "8 percent safe margin from all four edges. Never crop, obscure, or cut off the primary subject or its "
    "important details. The main subject should occupy about 35 to 60 percent of the frame, with intentional "
    "negative space and no extreme close-up. Compose for a final 1.91:1 crop and keep important details within "
    "the central 80 percent of the image height. "
)


def fit_image_to_jpeg(
    raw: bytes,
    *,
    width: int,
    height: int,
    quality: int,
) -> bytes:
    """画像を変形させず、中央トリミングで指定サイズのJPEGへ仕上げる。"""
    source = Image.open(io.BytesIO(raw)).convert("RGB")
    fitted = ImageOps.fit(
        source,
        (width, height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    output = io.BytesIO()
    fitted.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()
