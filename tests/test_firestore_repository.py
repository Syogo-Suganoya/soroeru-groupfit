"""Firestore 実装の統合テスト（エミュレータ相手）。

FIRESTORE_EMULATOR_HOST が無い環境では skip する。compose で開発している場合は常に走る。
既定のデータ管理先が Firestore なので、ここが通らないと本番も動かない。
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from app.domain.models import (
    AuditAction,
    AuditLog,
    Consent,
    EventInfo,
    Fitting,
    Member,
    MemberState,
    Notification,
    NotificationKind,
    Room,
    RoomStatus,
    TryOnResult,
)
from app.domain import catalog
from app.ports.repository import FirestoreRoomRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore エミュレータが無い環境ではスキップ",
)


@pytest.fixture
def repo() -> FirestoreRoomRepository:
    return FirestoreRoomRepository(os.getenv("GOOGLE_CLOUD_PROJECT", "soroeru-groupfit-local"))


def build_room() -> Room:
    """入れ子・列挙・日時・Optional を一通り含んだルームを作る。"""
    member = Member(
        display_name="花子",
        state=MemberState.confirmed,
        consent=Consent().grant(),
    )
    garment = catalog.find("g_navy_satin")
    assert garment is not None
    room = Room(
        event=EventInfo(
            title="友人の結婚式",
            event_date=date.today() + timedelta(days=30),
            dress_code="白NG",
        ),
        status=RoomStatus.open,
        members=[member],
    )
    room.fittings[member.uid] = Fitting(
        member_uid=member.uid,
        candidates=[
            TryOnResult(garment=garment, image_ref="ref/a.png", tone_match=0.8)
        ],
        selected_garment_id=garment.garment_id,
    )
    room.notifications.append(
        Notification(to_uid=member.uid, kind=NotificationKind.pending, text="未確定です")
    )
    return room


async def test_room_survives_a_save_and_load_roundtrip(repo):
    room = build_room()
    await repo.save(room)

    loaded = await repo.get(room.room_id)
    assert loaded is not None

    # 型が落ちずに戻ること（date / Enum / 入れ子 / Optional）
    assert loaded.event.event_date == room.event.event_date
    assert loaded.status == RoomStatus.open
    assert loaded.members[0].display_name == "花子"
    assert loaded.members[0].consent.granted is True
    assert loaded.members[0].composable is True
    assert loaded.fittings[room.members[0].uid].selected.garment.garment_id == "g_navy_satin"
    assert loaded.notifications[0].kind == NotificationKind.pending
    assert loaded.notifications[0].read_at is None

    await repo.delete(room.room_id)
    assert await repo.get(room.room_id) is None


async def test_get_returns_none_for_unknown_room(repo):
    assert await repo.get("room_does_not_exist") is None


async def test_audits_are_scoped_to_the_room_and_ordered(repo):
    room = build_room()
    other_room_id = "room_other"
    for action in (AuditAction.room_create, AuditAction.consent_grant, AuditAction.purge):
        await repo.append_audit(
            AuditLog(room_id=room.room_id, actor="tester", action=action)
        )
    await repo.append_audit(
        AuditLog(room_id=other_room_id, actor="tester", action=AuditAction.room_create)
    )

    logs = await repo.audits(room.room_id)
    assert [log.action for log in logs] == [
        AuditAction.room_create,
        AuditAction.consent_grant,
        AuditAction.purge,
    ]
    assert all(log.room_id == room.room_id for log in logs)


async def test_audit_survives_room_deletion(repo):
    """削除の証跡そのものが必要なので、ルームを消しても監査ログは残る。"""
    room = build_room()
    await repo.save(room)
    await repo.append_audit(
        AuditLog(room_id=room.room_id, actor="organizer", action=AuditAction.purge)
    )

    await repo.delete(room.room_id)

    assert await repo.get(room.room_id) is None
    assert [log.action for log in await repo.audits(room.room_id)] == [AuditAction.purge]


async def test_list_rooms_includes_saved_rooms(repo):
    room = build_room()
    await repo.save(room)
    try:
        ids = [r.room_id for r in await repo.list_rooms()]
        assert room.room_id in ids
    finally:
        await repo.delete(room.room_id)
