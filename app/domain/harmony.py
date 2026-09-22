"""調和判定ロジック。

- 色かぶり: 主要色の CIEDE2000 色差がしきい値未満のペアを警告
- 柄かぶり: 同一柄ファミリで無地以外のペアを警告
- 浮き: フォーマル度のグループ中央値から threshold 以上乖離
- ドレスコード: シーン別プリセット＋自由記述の読み取り結果（EventInfo.dress_rules）で検証

いずれも evidence に数値を必ず入れる（提案の透明性のため）。純関数として書き、
LLM を通さずに再現可能にしてある。Gemini が関わるのは、判定の入力になる
ドレスコードの読み取り（ルーム作成時に1回・結果は保存）と、判定後の総評の言語化だけ。
"""

from __future__ import annotations

from statistics import median

from app.domain.color import delta_e_hex, hue_degrees
from app.domain.dresscode import effective_preset
from app.domain.models import (
    Fitting,
    HarmonyReport,
    HarmonyWarning,
    Member,
    PatternFamily,
    Room,
    Severity,
    WarningKind,
)


def _name_of(room: Room, uid: str) -> str:
    m = room.member(uid)
    return m.display_name if m else uid


def evaluate(
    room: Room,
    *,
    color_clash_delta_e: float,
    formality_gap_threshold: float,
) -> HarmonyReport:
    """確定衣装のあるメンバーだけを対象に調和を評価する。"""
    fittings = room.confirmed_fittings()
    warnings: list[HarmonyWarning] = []

    if not fittings:
        return HarmonyReport(warnings=[], formality_median=None)

    # ---------------------------------------------------------- ドレスコード
    preset = effective_preset(room.event)
    for uid, fitting in fittings.items():
        garment = fitting.selected.garment  # type: ignore[union-attr]
        for hit in preset.check(garment):
            warnings.append(
                HarmonyWarning(
                    kind=WarningKind.dress_code,
                    severity=hit.severity,
                    member_uids=[uid],
                    message=f"{_name_of(room, uid)}さん: {hit.message}",
                    evidence={**hit.evidence, "rule": hit.rule, "scene": preset.label},
                    suggestions=hit.suggestions,
                )
            )

    # ---------------------------------------------------------- 色・柄かぶり
    uids = sorted(fittings)
    for i, a_uid in enumerate(uids):
        for b_uid in uids[i + 1 :]:
            ga = fittings[a_uid].selected.garment  # type: ignore[union-attr]
            gb = fittings[b_uid].selected.garment  # type: ignore[union-attr]

            delta = delta_e_hex(ga.primary_color_hex, gb.primary_color_hex)
            if delta < color_clash_delta_e:
                warnings.append(
                    HarmonyWarning(
                        kind=WarningKind.color_clash,
                        severity=Severity.warn,
                        member_uids=[a_uid, b_uid],
                        message=(
                            f"{_name_of(room, a_uid)}さんと{_name_of(room, b_uid)}さんの色が"
                            f"近すぎます（{ga.color_name} / {gb.color_name}）。"
                        ),
                        evidence={
                            "delta_e_2000": round(delta, 2),
                            "threshold": color_clash_delta_e,
                            "colors": [ga.primary_color_hex, gb.primary_color_hex],
                            "hue_degrees": [
                                round(hue_degrees(ga.primary_color_hex), 1),
                                round(hue_degrees(gb.primary_color_hex), 1),
                            ],
                        },
                        suggestions=[
                            "どちらかを色相環で60度以上離す",
                            "明度差をつけて濃淡で分ける",
                        ],
                    )
                )

            if (
                ga.pattern == gb.pattern
                and ga.pattern != PatternFamily.solid
            ):
                warnings.append(
                    HarmonyWarning(
                        kind=WarningKind.pattern_clash,
                        severity=Severity.warn,
                        member_uids=[a_uid, b_uid],
                        message=(
                            f"{_name_of(room, a_uid)}さんと{_name_of(room, b_uid)}さんの柄が"
                            f"同系統です（{ga.pattern.value}）。"
                        ),
                        evidence={"pattern": ga.pattern.value},
                        suggestions=["片方を無地に寄せる", "柄の大きさを変える"],
                    )
                )

    # ---------------------------------------------------------- 浮き
    scores = {uid: f.selected.garment.formality for uid, f in fittings.items()}  # type: ignore[union-attr]
    med = median(scores.values())
    for uid, score in scores.items():
        gap = abs(score - med)
        if gap >= formality_gap_threshold:
            direction = "フォーマル寄り" if score > med else "カジュアル寄り"
            warnings.append(
                HarmonyWarning(
                    kind=WarningKind.formality_outlier,
                    severity=Severity.warn,
                    member_uids=[uid],
                    message=(
                        f"{_name_of(room, uid)}さんだけがグループの中で{direction}です"
                        f"（フォーマル度 {score} / 中央値 {med:g}）。"
                    ),
                    evidence={
                        "formality": score,
                        "group_median": med,
                        "gap": round(gap, 1),
                        "threshold": formality_gap_threshold,
                        "all_scores": scores,
                    },
                    suggestions=(
                        ["羽織り・小物で格を落とす"]
                        if score > med
                        else ["ジャケットやアクセサリーで格を上げる"]
                    ),
                )
            )

    return HarmonyReport(warnings=warnings, formality_median=float(med))


def pending_members(room: Room) -> list[Member]:
    """まだ衣装を確定していない参加者（手配エージェントのリマインド対象）。"""
    confirmed = room.confirmed_fittings()
    return [m for m in room.active_members if m.uid not in confirmed]


def summarize(report: HarmonyReport) -> str:
    """LLM を使わないフォールバック総評。"""
    if not report.warnings:
        return "現時点でかぶり・浮きは検出されていません。"
    counts: dict[str, int] = {}
    for w in report.warnings:
        counts[w.kind.value] = counts.get(w.kind.value, 0) + 1
    label = {
        "color_clash": "色かぶり",
        "pattern_clash": "柄かぶり",
        "formality_outlier": "浮き",
        "dress_code": "ドレスコード",
    }
    parts = [f"{label.get(k, k)}{v}件" for k, v in counts.items()]
    return "検出: " + " / ".join(parts)


__all__ = ["evaluate", "pending_members", "summarize", "Fitting"]
