"""バーチャル試着のポート。

現状は Pillow で「その人の色」の人物カードを描く。実際の試着エンジンに
差し替えるときも、呼び出し側（試着エージェント）は変更不要にしてある。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Garment, TryOnResult
from app.ports.storage import StoragePort
from app.rendering import figure

SEASONS = ["spring", "summer", "autumn", "winter"]


class TryOnPort(ABC):
    name = "tryon"
    engine = "mock"

    @abstractmethod
    async def try_on(
        self, *, room_id: str, member_uid: str, member_name: str, garment: Garment
    ) -> TryOnResult:
        """1着分の試着画像を生成する。"""

    @abstractmethod
    async def analyze_tone(self, *, room_id: str, member_uid: str) -> str:
        """パーソナルカラー（spring / summer / autumn / winter）を返す。"""


class MockTryOnPort(TryOnPort):
    """外部キー不要のローカル生成。デモとテストはこれで完結する。"""

    name = "mock-tryon"
    engine = "mock"

    def __init__(self, storage: StoragePort) -> None:
        self.storage = storage

    def _tone_of(self, member_uid: str) -> str:
        return SEASONS[sum(member_uid.encode()) % len(SEASONS)]

    async def analyze_tone(self, *, room_id: str, member_uid: str) -> str:
        return self._tone_of(member_uid)

    async def try_on(
        self, *, room_id: str, member_uid: str, member_name: str, garment: Garment
    ) -> TryOnResult:
        png = figure.render_try_on_card(
            member_name=member_name,
            garment_name=garment.name,
            color_hex=garment.primary_color_hex,
            pattern=garment.pattern.value,
        )
        ref = await self.storage.put(
            room_id=room_id,
            key=f"fittings/{member_uid}/{garment.garment_id}.png",
            data=png,
            content_type="image/png",
        )
        tone = self._tone_of(member_uid)
        score = figure.tone_match_score(tone, garment.primary_color_hex)
        return TryOnResult(
            garment=garment,
            image_ref=ref,
            tone_match=score,
            note=f"パーソナルカラー {tone} との適合度 {score:.0%}",
            engine=self.engine,
        )
