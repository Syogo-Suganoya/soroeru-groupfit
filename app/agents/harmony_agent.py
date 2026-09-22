"""調和エージェント。

かぶり・浮きの検知と代替案提示。検知・提案は自律で行うが、衣装の変更は行わない。
数値判定はドメインの純関数、ドレスコードの読み取りと総評の言語化は Gemini という分担にしている。
"""

from __future__ import annotations

import hashlib
import json

from app.domain import catalog, harmony as rules
from app.domain.color import delta_e_hex
from app.domain.dresscode import effective_preset
from app.domain.models import DressRules, Garment, HarmonyReport, Room, Severity, WarningKind
from app.ports.llm import LlmPort, Summary


class HarmonyAgent:
    name = "harmony-agent"

    def __init__(
        self, llm: LlmPort, *, color_clash_delta_e: float, formality_gap_threshold: float
    ) -> None:
        self.llm = llm
        self.color_clash_delta_e = color_clash_delta_e
        self.formality_gap_threshold = formality_gap_threshold

    async def interpret_dress_code(self, text: str | None) -> DressRules | None:
        """ルーム作成時に1回だけ呼ぶ。結果を保存し、以後の判定はその保存値で行う。"""
        if not text or not text.strip():
            return None
        return await self.llm.interpret_dress_code(text=text.strip())

    async def evaluate(self, room: Room) -> HarmonyReport:
        report = rules.evaluate(
            room,
            color_clash_delta_e=self.color_clash_delta_e,
            formality_gap_threshold=self.formality_gap_threshold,
        )
        confirmed = room.confirmed_fittings()
        if not confirmed:
            return report  # 誰も決めていない段階で「揃っています」とは言えない

        # advance() は同意・参加・試着でも走る。総評の入力（確定衣装と指摘）が
        # 変わっていなければ前回の文を使い回し、Gemini を呼ばない。
        # ただし前回が Gemini 障害時の代替文だった場合は、次の機会に作り直す。
        key = self._summary_key(room, report)
        previous = room.harmony
        if (
            previous.explanation
            and previous.explanation_key == key
            and previous.explanation_by == self.llm.engine
        ):
            summary = Summary(text=previous.explanation, by=previous.explanation_by)
        else:
            summary = await self.llm.summarize_harmony(room=room, report=report)
        report.explanation = summary.text
        report.explanation_by = summary.by
        report.explanation_key = key
        return report

    @staticmethod
    def _summary_key(room: Room, report: HarmonyReport) -> str:
        confirmed = room.confirmed_fittings()
        basis = {
            "scene": room.event.scene.value,
            "dress_code": room.event.dress_code,
            # 表示名は総評の文中に出るので、変われば作り直す
            "members": sorted(
                (
                    (room.member(uid).display_name if room.member(uid) else uid),
                    f.selected_garment_id,
                )
                for uid, f in confirmed.items()
            ),
            "undecided": len(room.active_members) - len(confirmed),
            "warnings": [(w.kind.value, w.severity.value, w.message) for w in report.warnings],
        }
        raw = json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

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
        preset = effective_preset(room.event)
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
