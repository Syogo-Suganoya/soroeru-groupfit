"""色差計算（CIEDE2000）。設計書 §4「色かぶり」判定の数値的な根拠。

外部依存を増やさないため sRGB → XYZ → CIE Lab → ΔE00 を自前で実装する。
判定根拠として UI に出す値なので、途中の L*a*b* もそのまま返せるようにしてある。
"""

from __future__ import annotations

import math

# D65 白色点
_WHITE = (95.047, 100.000, 108.883)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    s = value.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"不正なカラーコード: {value}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def rgb_to_xyz(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def linear(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) * 100.0 for c in rgb)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    return x, y, z


def xyz_to_lab(xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = (f(v / w) for v, w in zip(xyz, _WHITE))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def hex_to_lab(value: str) -> tuple[float, float, float]:
    return xyz_to_lab(rgb_to_xyz(hex_to_rgb(value)))


def delta_e_2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 色差。0 に近いほど「同じ色に見える」。"""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    avg_l = (l1 + l2) / 2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    avg_c = (c1 + c2) / 2

    g = 0.5 * (1 - math.sqrt(avg_c**7 / (avg_c**7 + 25**7))) if avg_c > 0 else 0.0
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    avg_cp = (c1p + c2p) / 2

    def hue(ap: float, bp: float) -> float:
        if ap == 0 and bp == 0:
            return 0.0
        h = math.degrees(math.atan2(bp, ap))
        return h + 360 if h < 0 else h

    h1p, h2p = hue(a1p, b1), hue(a2p, b2)

    if c1p * c2p == 0:
        dhp = 0.0
        avg_hp = h1p + h2p
    else:
        dh = h2p - h1p
        if dh > 180:
            dh -= 360
        elif dh < -180:
            dh += 360
        dhp = dh
        avg_hp = (h1p + h2p + (360 if abs(h1p - h2p) > 180 else 0)) / 2

    dlp = l2 - l1
    dcp = c2p - c1p
    dhp_term = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)

    t = (
        1
        - 0.17 * math.cos(math.radians(avg_hp - 30))
        + 0.24 * math.cos(math.radians(2 * avg_hp))
        + 0.32 * math.cos(math.radians(3 * avg_hp + 6))
        - 0.20 * math.cos(math.radians(4 * avg_hp - 63))
    )

    sl = 1 + (0.015 * (avg_l - 50) ** 2) / math.sqrt(20 + (avg_l - 50) ** 2)
    sc = 1 + 0.045 * avg_cp
    sh = 1 + 0.015 * avg_cp * t

    d_theta = 30 * math.exp(-(((avg_hp - 275) / 25) ** 2))
    rc = 2 * math.sqrt(avg_cp**7 / (avg_cp**7 + 25**7)) if avg_cp > 0 else 0.0
    rt = -rc * math.sin(2 * math.radians(d_theta))

    return math.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dhp_term / sh) ** 2
        + rt * (dcp / sc) * (dhp_term / sh)
    )


def delta_e_hex(hex1: str, hex2: str) -> float:
    return delta_e_2000(hex_to_lab(hex1), hex_to_lab(hex2))


def is_whiteish(hex_value: str, *, l_min: float = 85.0, chroma_max: float = 12.0) -> bool:
    """結婚式の白 NG 判定。明度が高く彩度が低いものを「白扱い」とする。

    オフホワイト・アイボリー・シャンパンも花嫁の色に触れるため同じ扱い。
    """
    l, a, b = hex_to_lab(hex_value)
    return l >= l_min and math.hypot(a, b) <= chroma_max


def is_blackish(hex_value: str, *, l_max: float = 25.0, chroma_max: float = 12.0) -> bool:
    """全身黒 NG 判定。

    暗いだけでは足りない。濃紺やダークグリーンは慶事で許容されるため、
    彩度も低いこと（無彩色に近いこと）を条件に加える。
    """
    l, a, b = hex_to_lab(hex_value)
    return l <= l_max and math.hypot(a, b) <= chroma_max


def hue_degrees(hex_value: str) -> float:
    """色相環表示用（§7-3 で UI に根拠として出す）。"""
    _, a, b = hex_to_lab(hex_value)
    h = math.degrees(math.atan2(b, a))
    return h + 360 if h < 0 else h


def personal_color_season(
    skin_hex: str, *, warm_ratio: float = 1.75, light_l: float = 60.0
) -> str:
    """肌色の16進値から4シーズン（spring/summer/autumn/winter）を推定する。

    YouCam の AI Facial Color Tones Analyzer は色の16進値までしか返さないため、
    シーズン分類はこちらで行う。判定軸は次の2つ:

    - **イエベ / ブルベ**: b*（黄-青）と a*（赤-緑）の比。肌の a* b* はどちらも正で、
      黄みが赤みに対して強いほどイエローベース寄りになる。
    - **明るい / 深い**: L*（明度）。

    肌色は個人差より照明差のほうが大きく出るため、これは目安であって断定ではない。
    提示順の並び替えにだけ使い、「あなたは○○です」とは言わない（設計書 §7-3）。
    """
    l, a, b = hex_to_lab(skin_hex)
    warm = b >= a * warm_ratio
    light = l >= light_l
    if warm:
        return "spring" if light else "autumn"
    return "summer" if light else "winter"
