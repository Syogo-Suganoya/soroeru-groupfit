from app.domain.color import delta_e_hex, is_blackish, is_whiteish


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
