"""試着カタログ（MVP はローカル固定。実運用ではレンタルEC由来に差し替える）。

主要色・柄・フォーマル度をカタログ側の一次情報として持つことで、
調和判定を LLM に依存せず再現可能にしている。
"""

from __future__ import annotations

from app.domain.models import Garment, PatternFamily

WEDDING_CATALOG: list[Garment] = [
    Garment(
        garment_id="g_navy_satin",
        name="ネイビーサテンドレス",
        category="ドレス",
        primary_color_hex="#1F2A5A",
        color_name="ネイビー",
        pattern=PatternFamily.solid,
        formality=4,
        price_yen=9800,
        rental_url="https://example.com/rental/g_navy_satin",
    ),
    Garment(
        garment_id="g_navy_lace",
        name="ネイビーレースドレス",
        category="ドレス",
        primary_color_hex="#22305F",
        color_name="ネイビー",
        pattern=PatternFamily.lace,
        formality=4,
        price_yen=11800,
        rental_url="https://example.com/rental/g_navy_lace",
    ),
    Garment(
        garment_id="g_bordeaux",
        name="ボルドーベロアドレス",
        category="ドレス",
        primary_color_hex="#6E1E33",
        color_name="ボルドー",
        pattern=PatternFamily.solid,
        formality=4,
        price_yen=10800,
        rental_url="https://example.com/rental/g_bordeaux",
    ),
    Garment(
        garment_id="g_sage",
        name="セージグリーンドレス",
        category="ドレス",
        primary_color_hex="#8FA98A",
        color_name="セージグリーン",
        pattern=PatternFamily.solid,
        formality=3,
        price_yen=8800,
        rental_url="https://example.com/rental/g_sage",
    ),
    Garment(
        garment_id="g_dusty_blue",
        name="くすみブルードレス",
        category="ドレス",
        primary_color_hex="#6E88A6",
        color_name="ダスティブルー",
        pattern=PatternFamily.solid,
        formality=4,
        price_yen=9800,
        rental_url="https://example.com/rental/g_dusty_blue",
    ),
    Garment(
        garment_id="g_terracotta",
        name="テラコッタドレス",
        category="ドレス",
        primary_color_hex="#B26E4E",
        color_name="テラコッタ",
        pattern=PatternFamily.solid,
        formality=3,
        price_yen=8400,
        rental_url="https://example.com/rental/g_terracotta",
    ),
    Garment(
        garment_id="g_floral_pink",
        name="ピンク花柄ドレス",
        category="ドレス",
        primary_color_hex="#D98BA5",
        color_name="ローズピンク",
        pattern=PatternFamily.floral,
        formality=3,
        price_yen=7800,
        rental_url="https://example.com/rental/g_floral_pink",
    ),
    Garment(
        garment_id="g_floral_green",
        name="グリーン花柄ドレス",
        category="ドレス",
        primary_color_hex="#5F7F5A",
        color_name="フォレストグリーン",
        pattern=PatternFamily.floral,
        formality=3,
        price_yen=7800,
        rental_url="https://example.com/rental/g_floral_green",
    ),
    Garment(
        garment_id="g_black_formal",
        name="ブラックフォーマルドレス",
        category="ドレス",
        primary_color_hex="#111114",
        color_name="ブラック",
        pattern=PatternFamily.solid,
        formality=5,
        price_yen=9200,
        rental_url="https://example.com/rental/g_black_formal",
    ),
    Garment(
        garment_id="g_ivory",
        name="アイボリードレス",
        category="ドレス",
        primary_color_hex="#F2ECE1",
        color_name="アイボリー",
        pattern=PatternFamily.solid,
        formality=4,
        price_yen=9800,
        rental_url="https://example.com/rental/g_ivory",
    ),
    Garment(
        garment_id="g_fur_beige",
        name="ファートリムワンピース",
        category="ドレス",
        primary_color_hex="#C6A88A",
        color_name="ベージュ",
        pattern=PatternFamily.solid,
        formality=3,
        has_fur=True,
        price_yen=8800,
        rental_url="https://example.com/rental/g_fur_beige",
    ),
    Garment(
        garment_id="g_casual_denim",
        name="デニムワンピース",
        category="ワンピース",
        primary_color_hex="#4E6E93",
        color_name="デニムブルー",
        pattern=PatternFamily.solid,
        formality=1,
        price_yen=4800,
        rental_url="https://example.com/rental/g_casual_denim",
    ),
]

_BY_ID = {g.garment_id: g for g in WEDDING_CATALOG}


def all_garments() -> list[Garment]:
    return list(WEDDING_CATALOG)


def find(garment_id: str) -> Garment | None:
    return _BY_ID.get(garment_id)
