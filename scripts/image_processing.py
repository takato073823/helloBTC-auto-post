"""アイキャッチ画像の縦横比を保った仕上げ処理。"""

import io

from PIL import Image, ImageOps


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
