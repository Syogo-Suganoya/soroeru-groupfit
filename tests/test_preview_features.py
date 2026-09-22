"""集合プレビューのローカル合成のテスト。

ローカル実装だけで体験が成立し、同意の約束（未同意はシルエット）が
崩れないことを確かめる。
"""

from __future__ import annotations

import io

from PIL import Image

from app.ports.compositor import LocalCompositor

NAVY = (0x1F, 0x2A, 0x5A)


def figures(*, silhouette: bool) -> list[dict]:
    return [{"name": "A", "color_hex": "#1F2A5A", "pattern": "solid", "silhouette": silhouette}]


def has_color(png: bytes, rgb: tuple[int, int, int]) -> bool:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    return rgb in {c for _, c in img.getcolors(maxcolors=1 << 24)}


async def test_consented_member_is_drawn_in_their_garment_color():
    png = await LocalCompositor().compose_group(figures=figures(silhouette=False))
    assert has_color(png, NAVY)


async def test_silhouette_hides_the_garment_color():
    # 未同意の人は、衣装が決まっていてもその色で描かない（誰が何を着るかも出さない）
    png = await LocalCompositor().compose_group(figures=figures(silhouette=True))
    assert not has_color(png, NAVY)


async def test_composition_is_deterministic():
    # 同じ入力なら同じ絵になる。advance() のたびに作り直しても見た目が揺れない
    a = await LocalCompositor().compose_group(figures=figures(silhouette=False))
    b = await LocalCompositor().compose_group(figures=figures(silhouette=False))
    assert a == b
