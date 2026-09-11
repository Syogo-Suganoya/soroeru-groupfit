import pytest

from app.domain.color import (
    delta_e_hex,
    is_blackish,
    is_whiteish,
    personal_color_season,
)


def test_identical_colors_have_zero_delta():
    assert delta_e_hex("#1F2A5A", "#1F2A5A") == 0.0


def test_near_identical_navies_are_below_clash_threshold():
    # ネイビー同士 = 並ぶとかぶって見える
    assert delta_e_hex("#1F2A5A", "#22305F") < 12.0


def test_distinct_colors_are_above_clash_threshold():
    assert delta_e_hex("#1F2A5A", "#B26E4E") > 12.0


def test_white_detection_covers_ivory():
    assert is_whiteish("#FFFFFF")
    assert is_whiteish("#F2ECE1")  # アイボリーも花嫁の色として扱う
    assert not is_whiteish("#8FA98A")


def test_black_detection():
    assert is_blackish("#111114")
    assert not is_blackish("#6E88A6")


# --- パーソナルカラー（YouCam の skin_color から4シーズンを起こす） ---


@pytest.mark.parametrize(
    "skin_hex,expected",
    [
        ("#e0b89a", "spring"),  # 明るい・黄み寄り
        ("#eecfc0", "summer"),  # 明るい・赤み寄り
        ("#a87f5f", "autumn"),  # 深い・黄み寄り
        ("#4a3123", "winter"),  # 深い・赤み寄り
    ],
)
def test_skin_color_maps_to_a_season(skin_hex, expected):
    assert personal_color_season(skin_hex) == expected


def test_season_is_always_one_of_the_four():
    # 肌色以外が来ても呼び出し側が壊れないこと
    for hex_value in ["#FFFFFF", "#000000", "#8FA98A", "#b9947c"]:
        assert personal_color_season(hex_value) in {
            "spring",
            "summer",
            "autumn",
            "winter",
        }
