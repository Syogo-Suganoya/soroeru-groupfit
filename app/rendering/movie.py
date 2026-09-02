"""記念ムービーのローカル生成（設計書 §12 の image-to-video のフォールバック）。

GMI Cloud の image-to-video が使えないときでも体験が成立するよう、
集合プレビュー1枚からゆっくり寄る短いアニメーションGIFを作る。
音楽はローカルでは付かない（GMI の音楽生成に相当する処理を持たない）。
"""

from __future__ import annotations

import io

from PIL import Image

FRAMES = 24
SECONDS = 3.0
ZOOM_END = 1.12  # 最終フレームの拡大率。寄りすぎると顔が切れるので控えめにする。


def render_keepsake_gif(
    preview_png: bytes, *, frames: int = FRAMES, seconds: float = SECONDS
) -> tuple[bytes, float]:
    """集合プレビューから、ゆっくり寄るGIFを作って (バイト列, 秒数) を返す。"""
    base = Image.open(io.BytesIO(preview_png)).convert("RGB")
    width, height = base.size

    images: list[Image.Image] = []
    for i in range(frames):
        # 0 → 1 で進む。イーズアウトで終盤をゆるめる。
        t = i / max(1, frames - 1)
        eased = 1 - (1 - t) ** 2
        zoom = 1 + (ZOOM_END - 1) * eased

        crop_w, crop_h = width / zoom, height / zoom
        left = (width - crop_w) / 2
        # 人物は上半分にいるので、寄るときは少し上を残す
        top = (height - crop_h) * 0.35
        frame = base.crop(
            (int(left), int(top), int(left + crop_w), int(top + crop_h))
        ).resize((width, height), Image.LANCZOS)
        images.append(frame)

    buf = io.BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=int(seconds * 1000 / frames),
        loop=0,
        optimize=True,
    )
    return buf.getvalue(), seconds
