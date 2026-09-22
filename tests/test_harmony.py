"""調和判定のテスト。判定は純関数なので LLM 抜きで検証できる。"""

from __future__ import annotations

from datetime import date, timedelta

from app.domain import catalog
from app.domain.harmony import evaluate
from app.domain.models import (
    EventInfo,
    Fitting,
    Member,
    MemberState,
    Room,
    TryOnResult,
    WarningKind,
)

THRESHOLDS = {"color_clash_delta_e": 12.0, "formality_gap_threshold": 2.0}


def build_room(assignments: dict[str, str]) -> Room:
    """{表示名: garment_id} からルームを組み立てる。"""
    room = Room(event=EventInfo(title="結婚式", event_date=date.today() + timedelta(days=30)))
    for name, gid in assignments.items():
        member = Member(display_name=name, state=MemberState.confirmed)
        room.members.append(member)
        garment = catalog.find(gid)
        assert garment is not None
        room.fittings[member.uid] = Fitting(
            member_uid=member.uid,
            candidates=[TryOnResult(garment=garment, image_ref=f"ref/{gid}.png")],
            selected_garment_id=gid,
        )
    return room


def kinds(room: Room) -> list[WarningKind]:
    return [w.kind for w in evaluate(room, **THRESHOLDS).warnings]


def test_no_warning_for_distinct_outfits():
    room = build_room({"A": "g_navy_satin", "B": "g_bordeaux", "C": "g_sage"})
    report = evaluate(room, **THRESHOLDS)
    assert report.warnings == []
    assert report.formality_median == 4.0


def test_color_clash_between_two_navies():
    room = build_room({"A": "g_navy_satin", "B": "g_navy_lace"})
    report = evaluate(room, **THRESHOLDS)
    clashes = [w for w in report.warnings if w.kind == WarningKind.color_clash]
    assert len(clashes) == 1
    # 根拠の数値を必ず持つ
    assert clashes[0].evidence["delta_e_2000"] < 12.0
    assert len(clashes[0].member_uids) == 2


def test_pattern_clash_between_two_florals():
    room = build_room({"A": "g_floral_pink", "B": "g_floral_green"})
    assert WarningKind.pattern_clash in kinds(room)


def test_formality_outlier_detected():
    room = build_room({"A": "g_navy_satin", "B": "g_bordeaux", "C": "g_casual_denim"})
    outliers = [
        w for w in evaluate(room, **THRESHOLDS).warnings
        if w.kind == WarningKind.formality_outlier
    ]
    assert len(outliers) == 1
    assert outliers[0].evidence["formality"] == 1
    assert outliers[0].evidence["group_median"] == 4


def test_dress_code_blocks_white_and_fur():
    room = build_room({"A": "g_ivory", "B": "g_fur_beige"})
    dress = [w for w in evaluate(room, **THRESHOLDS).warnings if w.kind == WarningKind.dress_code]
    rules = {w.evidence["rule"] for w in dress}
    assert "white" in rules
    assert "fur" in rules
    assert all(w.severity.value == "block" for w in dress if w.evidence["rule"] in {"white", "fur"})


def test_all_black_is_warned_not_blocked():
    room = build_room({"A": "g_black_formal", "B": "g_bordeaux"})
    black = [w for w in evaluate(room, **THRESHOLDS).warnings if w.evidence.get("rule") == "all_black"]
    assert len(black) == 1
    assert black[0].severity.value == "warn"


def test_members_without_confirmed_outfit_are_ignored():
    room = build_room({"A": "g_navy_satin"})
    room.members.append(Member(display_name="未確定", state=MemberState.joined))
    assert evaluate(room, **THRESHOLDS).warnings == []
