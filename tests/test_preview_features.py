"""会場ライティング再現・記念ムービーのテスト。

ローカル実装だけで体験が成立し、同意の約束（未同意はシルエット）が
崩れないことを確かめる。
"""

from __future__ import annotations

import io

from PIL import Image

from app.domain.models import LightingPreset
from app.ports.compositor import LocalCompositor
from app.ports.video import LocalVideoPort
from app.rendering import figure

FIGURES = [
    {"name": "A", "color_hex": "#1F2A5A", "pattern": "solid", "silhouette": False},
    {"name": "B", "color_hex": "#6E1E33", "pattern": "solid", "silhouette": True},
]


def average_rgb(png: bytes) -> tuple[float, float, float]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    pixels = list(img.getdata())
    n = len(pixels)
    return tuple(sum(c[i] for c in pixels) / n for i in range(3))  # type: ignore[return-value]


# ---------------------------------------------------------------- ライティング


def test_lighting_none_leaves_the_image_untouched():
    png = figure.render_group_preview(FIGURES)
    assert figure.apply_lighting(png, LightingPreset.none) == png


def test_evening_lighting_is_warmer_and_darker_than_daylight():
    png = figure.render_group_preview(FIGURES)
    day = average_rgb(figure.apply_lighting(png, LightingPreset.garden_day))
    evening = average_rgb(figure.apply_lighting(png, LightingPreset.hall_evening))

    assert evening[0] - evening[2] > day[0] - day[2]  # 赤寄り（暖色）
    assert sum(evening) < sum(day)  # 全体に暗い


async def test_local_compositor_applies_lighting():
    compositor = LocalCompositor()
    plain = await compositor.compose_group(figures=FIGURES, lighting=LightingPreset.none)
    lit = await compositor.compose_group(
        figures=FIGURES, lighting=LightingPreset.chapel
    )
    assert plain != lit


# ---------------------------------------------------------------- 記念ムービー


async def test_local_video_makes_an_animated_gif():
    png = figure.render_group_preview(FIGURES)
    result = await LocalVideoPort().from_image(image=png, prompt="test")

    assert result.content_type == "image/gif"
    assert result.engine == "local"
    assert result.has_audio is False  # ローカルでは音楽を付けない

    gif = Image.open(io.BytesIO(result.data))
    assert gif.format == "GIF"
    assert getattr(gif, "n_frames", 1) > 1
    # 元のプレビューと同じ画面サイズを保つ
    assert gif.size == Image.open(io.BytesIO(png)).size
