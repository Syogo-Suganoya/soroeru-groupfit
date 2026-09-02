"""設計書 §12（GMI Cloud 活用）のテスト。

ローカル実装で体験が成立することと、GMI エンジンに差し替えても
同意の約束（未同意はシルエット）が崩れないことを確かめる。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.domain.models import LightingPreset
from app.ports.compositor import GmiCompositor, LocalCompositor
from app.ports.video import GmiVideoPort, LocalVideoPort
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


# ---------------------------------------------------------------- GMI エンジン


async def test_gmi_compositor_falls_back_to_local_when_the_api_fails():
    """外部が落ちてもルーム進行は止めない。"""
    compositor = GmiCompositor(
        api_key="dummy",
        base_url="http://127.0.0.1:9/unreachable",  # 到達しないアドレス
        image_model="seedream",
        relight_model="bria-fibo-relight",
        timeout=1.0,
    )
    png = await compositor.compose_group(figures=FIGURES, lighting=LightingPreset.none)
    assert png.startswith(b"\x89PNG")


async def test_gmi_video_falls_back_to_local_gif_when_the_api_fails():
    port = GmiVideoPort(
        api_key="dummy",
        base_url="http://127.0.0.1:9/unreachable",
        video_model="image-to-video",
        timeout=1.0,
    )
    result = await port.from_image(
        image=figure.render_group_preview(FIGURES), prompt="test"
    )
    assert result.engine == "local"  # フォールバックしたことが記録される
    assert result.content_type == "image/gif"


def test_gmi_prompt_never_asks_to_generate_faces_for_silhouettes():
    """未同意の人の顔を生成させない（設計書 §7-1）。"""
    prompt = GmiCompositor._build_prompt(FIGURES, LightingPreset.hall_evening)
    assert "silhouette" in prompt
    assert "1 of them" in prompt  # シルエット対象の人数が明示される
    assert LightingPreset.hall_evening.prompt in prompt


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"data": [{"b64_json": "aGk="}]}, b"hi"),
        ({"output": [{"b64": "aGk="}]}, b"hi"),
        ({"b64_json": "aGk="}, b"hi"),
    ],
)
async def test_gmi_response_shapes_are_tolerated(payload, expected):
    """応答形式が未確定なので、複数の形を受けられるようにしてある。"""
    assert await GmiCompositor._extract_image(payload) == expected


async def test_gmi_response_without_image_raises():
    with pytest.raises(ValueError):
        await GmiCompositor._extract_image({"error": "nope"})
