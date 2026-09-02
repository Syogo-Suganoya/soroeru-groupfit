"""調和エージェント（設計書 §4）。

かぶり・浮きの検知と代替案提示。検知・提案は自律で行うが、衣装の変更は行わない。
数値判定はドメインの純関数、言語化のみ Gemini という分担にしている。
"""

from __future__ import annotations

from app.domain import catalog, harmony as rules
from app.domain.color import delta_e_hex
from app.domain.dresscode import preset_for
from app.domain.models import Garment, HarmonyReport, Room, Severity, WarningKind
from app.ports.llm import LlmPort


class HarmonyAgent:
    name = "harmony-agent"

    def __init__(
        self, llm: LlmPort, *, color_clash_delta_e: float, formality_gap_threshold: float
    ) -> None:
        self.llm = llm
        self.color_clash_delta_e = color_clash_delta_e
        self.formality_gap_threshold = formality_gap_threshold

    async def evaluate(self, room: Room) -> HarmonyReport:
        report = rules.evaluate(
            room,
            color_clash_delta_e=self.color_clash_delta_e,
            formality_gap_threshold=self.formality_gap_threshold,
        )
        report.explanation = await self.llm.summarize_harmony(room=room, report=report)
        return report

    def alternatives(self, room: Room, *, uid: str, limit: int = 3) -> list[Garment]:
        """警告の当事者に出す代替衣装。

        - 他メンバーの確定色から色差が十分にあること
        - シーンのドレスコードに抵触しないこと
        - グループのフォーマル度中央値に近いこと
        """
        confirmed = room.confirmed_fittings()
        others = [
            f.selected.garment  # type: ignore[union-attr]
            for other_uid, f in confirmed.items()
            if other_uid != uid
        ]
        preset = preset_for(room.event.scene)
        median_formality = room.harmony.formality_median or 4.0

        scored: list[tuple[float, Garment]] = []
        for garment in catalog.all_garments():
            if any(hit.severity == Severity.block for hit in preset.check(garment)):
                continue
            if garment.formality < preset.formality_floor:
                continue
            if others:
                min_delta = min(
                    delta_e_hex(garment.primary_color_hex, o.primary_color_hex) for o in others
                )
                if min_delta < self.color_clash_delta_e:
                    continue
                if any(
                    o.pattern == garment.pattern and o.pattern.value != "solid" for o in others
                ):
                    continue
            else:
                min_delta = 100.0
            # 色差は大きいほど良く、フォーマル度は中央値に近いほど良い
            score = min_delta - abs(garment.formality - median_formality) * 8
            scored.append((score, garment))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [g for _, g in scored[:limit]]

    @staticmethod
    def warned_uids(report: HarmonyReport) -> list[str]:
        seen: list[str] = []
        for w in report.warnings:
            if w.severity == Severity.info:
                continue
            for uid in w.member_uids:
                if uid not in seen:
                    seen.append(uid)
        return seen

    @staticmethod
    def blocking(report: HarmonyReport) -> list[str]:
        """ドレスコードの明確な NG。手配前に必ず解消させる。"""
        return [
            w.message
            for w in report.warnings
            if w.severity == Severity.block and w.kind == WarningKind.dress_code
        ]
