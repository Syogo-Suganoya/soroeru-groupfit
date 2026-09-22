"""個人試着エージェント。

役割: 各メンバーの試着画像生成と候補提示。自律性は「提案まで」で、
どれを着るかは本人が選ぶ（エージェントは確定しない）。
"""

from __future__ import annotations

from app.domain import catalog
from app.domain.models import Fitting, Garment, Member, TryOnResult, now
from app.ports.tryon import TryOnPort


class FittingAgent:
    name = "fitting-agent"

    def __init__(self, tryon: TryOnPort) -> None:
        self.tryon = tryon

    def suggest_garments(
        self, *, exclude_ids: set[str] | None = None, limit: int = 3
    ) -> list[Garment]:
        """候補を選ぶ。他メンバーが確定済みの衣装は候補から外す。"""
        exclude = exclude_ids or set()
        return [g for g in catalog.all_garments() if g.garment_id not in exclude][:limit]

    async def run(
        self, *, room_id: str, member: Member, garments: list[Garment]
    ) -> Fitting:
        results: list[TryOnResult] = []
        for garment in garments:
            results.append(
                await self.tryon.try_on(
                    room_id=room_id,
                    member_uid=member.uid,
                    member_name=member.display_name,
                    garment=garment,
                )
            )
        # パーソナルカラー適合度の高い順に提示する（あくまで提案の並び）。
        results.sort(key=lambda r: r.tone_match, reverse=True)
        return Fitting(member_uid=member.uid, candidates=results, updated_at=now())
