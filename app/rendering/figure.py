"""試着カード・集合プレビューの描画（Pillow）。

YouCam を mock で動かすための最小の可視化。実APIに差し替えると
このモジュールは集合プレビューの「並べ方」だけを担う。

同意していないメンバーは必ずシルエット（顔なし・グレー）で描く。
描画側でも同意フラグを見ることで、合成の不変条件を二重に守る（設計書 §7-1）。
"""

from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from app.domain.color import hex_to_rgb, hue_degrees
from app.domain.models import LightingPreset

CARD_SIZE = (360, 480)
BACKDROP = (245, 242, 238)
SILHOUETTE = (176, 176, 182)
# _draw_person が origin から下に描く高さ（scale=1 のとき）。接地位置の計算に使う。
FIGURE_HEIGHT = 298

# 日本語が出せるフォントがあれば使う。無ければ描画は図形のみに落とす。
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text(draw: ImageDraw.ImageDraw, xy, s: str, *, size: int, fill) -> None:
    draw.text(xy, s, font=_font(size), fill=fill, anchor="mm")


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_person(
    draw: ImageDraw.ImageDraw,
    *,
    origin: tuple[int, int],
    scale: float,
    color: tuple[int, int, int],
    silhouette: bool,
    pattern: str = "solid",
) -> None:
    """簡易的な人型。silhouette=True なら顔を描かずグレー一色にする。"""
    x, y = origin
    s = scale
    body = SILHOUETTE if silhouette else color
    head = SILHOUETTE if silhouette else (238, 216, 196)

    # 頭
    r = 26 * s
    draw.ellipse([x - r, y, x + r, y + 2 * r], fill=head)
    # 首
    draw.rectangle([x - 8 * s, y + 2 * r - 4 * s, x + 8 * s, y + 2 * r + 10 * s], fill=head)
    # 衣装（上半身 + スカート）
    top = y + 2 * r + 6 * s
    draw.polygon(
        [
            (x - 34 * s, top),
            (x + 34 * s, top),
            (x + 26 * s, top + 90 * s),
            (x - 26 * s, top + 90 * s),
        ],
        fill=body,
    )
    hem = top + 90 * s
    draw.polygon(
        [
            (x - 26 * s, hem),
            (x + 26 * s, hem),
            (x + 62 * s, hem + 150 * s),
            (x - 62 * s, hem + 150 * s),
        ],
        fill=body,
    )

    if silhouette or pattern == "solid":
        return

    # 柄の表現（判定に使う PatternFamily と目視を対応させる）
    accent = tuple(min(255, c + 60) for c in body)
    if pattern == "floral":
        for i in range(9):
            cx = x + (i % 3 - 1) * 26 * s
            cy = hem + 24 * s + (i // 3) * 34 * s
            draw.ellipse([cx - 7 * s, cy - 7 * s, cx + 7 * s, cy + 7 * s], fill=accent)
    elif pattern == "geometric":
        for i in range(5):
            yy = hem + 18 * s + i * 26 * s
            draw.line([(x - 58 * s, yy), (x + 58 * s, yy)], fill=accent, width=int(4 * s) or 1)
    elif pattern == "lace":
        for i in range(12):
            cx = x - 50 * s + (i % 6) * 20 * s
            cy = hem + 30 * s + (i // 6) * 40 * s
            draw.arc(
                [cx - 9 * s, cy - 9 * s, cx + 9 * s, cy + 9 * s], 0, 360, fill=accent
            )


def render_try_on_card(
    *, member_name: str, garment_name: str, color_hex: str, pattern: str = "solid"
) -> bytes:
    img = Image.new("RGB", CARD_SIZE, BACKDROP)
    draw = ImageDraw.Draw(img)
    _draw_person(
        draw,
        origin=(CARD_SIZE[0] // 2, 60),
        scale=1.0,
        color=hex_to_rgb(color_hex),
        silhouette=False,
        pattern=pattern,
    )
    _text(draw, (CARD_SIZE[0] // 2, 30), member_name, size=20, fill=(60, 58, 56))
    _text(draw, (CARD_SIZE[0] // 2, CARD_SIZE[1] - 26), garment_name, size=18, fill=(90, 88, 86))
    return _to_png(img)


def render_group_preview(figures: list[dict]) -> bytes:
    """集合プレビュー。

    figures の各要素: {name, color_hex, pattern, silhouette(bool), label}
    """
    count = max(1, len(figures))
    width = min(1400, 200 * count + 80)
    height = 460
    ground = height - 60
    img = Image.new("RGB", (width, height), BACKDROP)
    draw = ImageDraw.Draw(img)

    # 足元のライン（並んだときの見え方を示す地面）
    draw.line([(0, ground), (width, ground)], fill=(222, 216, 208), width=3)

    step = (width - 80) / count
    scale = min(1.0, step / 200)
    # 人数が増えて縮んでも全員が同じ地面に立つように、足元から逆算して配置する。
    top = int(ground - FIGURE_HEIGHT * scale)
    for i, f in enumerate(figures):
        x = int(40 + step * (i + 0.5))
        _draw_person(
            draw,
            origin=(x, top),
            scale=scale,
            color=hex_to_rgb(f.get("color_hex") or "#B0B0B6"),
            silhouette=bool(f.get("silhouette")),
            pattern=f.get("pattern", "solid"),
        )
        _text(draw, (x, ground + 26), f.get("name", ""), size=18, fill=(70, 68, 66))
        label = f.get("label")
        if label:
            _text(draw, (x, ground + 48), label, size=14, fill=(140, 136, 132))

    return _to_png(img)


# 会場ライティングの近似パラメータ: (被せる色, 被せる強さ, 明度, コントラスト, 彩度)
_LIGHTING = {
    LightingPreset.garden_day: ((255, 246, 224), 0.10, 1.10, 1.02, 1.05),
    LightingPreset.hall_evening: ((255, 198, 128), 0.20, 0.88, 1.10, 0.95),
    LightingPreset.chapel: ((236, 244, 255), 0.14, 1.14, 0.95, 0.95),
}


def apply_lighting(png: bytes, lighting: LightingPreset) -> bytes:
    """会場の光環境を近似する（設計書 §12 会場ライティング再現のローカル版）。

    GMI Cloud の relight モデルの代わりに、色被せと明度・彩度の調整で「らしさ」を出す。
    物理的な再照明ではないため、当日の見え方の目安として扱う。
    """
    params = _LIGHTING.get(lighting)
    if params is None:
        return png

    tint, strength, brightness, contrast, saturation = params
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = Image.blend(img, Image.new("RGB", img.size, tint), strength)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Color(img).enhance(saturation)
    return _to_png(img)


def tone_match_score(tone: str, color_hex: str) -> float:
    """パーソナルカラーと衣装色の適合度（0-1）。mock 用の決定的な近似。

    各シーズンの代表色相との距離で評価する。実運用では YouCam の解析結果に置き換える。
    """
    centers = {"spring": 60.0, "summer": 240.0, "autumn": 30.0, "winter": 300.0}
    center = centers.get(tone, 180.0)
    diff = abs(hue_degrees(color_hex) - center)
    diff = min(diff, 360 - diff)
    return round(max(0.0, math.cos(math.radians(diff)) * 0.5 + 0.5), 2)
