"""バーチャル試着のポート（YouCam AI Clothes Try-On / Facial Color Tones Analyzer）。

mock 実装は Pillow で「その人の色」の人物カードを描き、実APIと同じ TryOnResult を返す。
live 実装に差し替えても呼び出し側（試着エージェント）は変更不要。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Garment, TryOnResult
from app.ports.storage import StoragePort
from app.rendering import figure


class TryOnPort(ABC):
    name = "tryon"

    @abstractmethod
    async def try_on(
        self, *, room_id: str, member_uid: str, member_name: str, garment: Garment
    ) -> TryOnResult:
        """1着分の試着画像を生成する。"""

    @abstractmethod
    async def analyze_tone(self, *, room_id: str, member_uid: str) -> str:
        """パーソナルカラー（Facial Color Tones Analyzer）を返す。"""


class MockTryOnPort(TryOnPort):
    """外部キー不要のローカル生成。デモとテストはこれで完結する。"""

    TONES = ["spring", "summer", "autumn", "winter"]

    def __init__(self, storage: StoragePort) -> None:
        self.storage = storage

    def _tone_of(self, member_uid: str) -> str:
        return self.TONES[sum(member_uid.encode()) % len(self.TONES)]

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
        )


class YouCamTryOnPort(TryOnPort):
    """live 実装。API キーが入るまで未使用（設計書 §5 スポンサーAPI）。"""

    def __init__(self, storage: StoragePort, api_key: str, secret_key: str) -> None:
        self.storage = storage
        self.api_key = api_key
        self.secret_key = secret_key

    async def try_on(
        self, *, room_id: str, member_uid: str, member_name: str, garment: Garment
    ) -> TryOnResult:
        raise NotImplementedError(
            "YouCam 実接続は未実装です。YOUCAM_MODE=mock で起動してください。"
        )

    async def analyze_tone(self, *, room_id: str, member_uid: str) -> str:
        raise NotImplementedError(
            "YouCam 実接続は未実装です。YOUCAM_MODE=mock で起動してください。"
        )
