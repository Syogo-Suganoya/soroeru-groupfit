"""ルーム進行と、設計書 §7 のガバナンス要件のテスト。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.domain.models import AuditAction, EventInfo, RoomStatus


async def setup_room(o, event, names=("幹事", "花子", "さくら")):
    room = await o.create_room(event=event, organizer_name=names[0])
    uids = [room.members[0].uid]
    for name in names[1:]:
        room, member = await o.join(room_id=room.room_id, display_name=name)
        uids.append(member.uid)
    return room, uids


async def test_room_progresses_to_arranged_when_all_confirmed(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    assert room.status == RoomStatus.open

    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    assert room.status == RoomStatus.open  # まだ1人

    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[1], garment_id="g_bordeaux"
    )
    assert room.all_confirmed
    assert room.status == RoomStatus.arranged
    assert len(room.arrange.items) == 2


async def test_preview_uses_silhouette_until_consent(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    assert room.preview.current.composed_uids == []
    assert set(room.preview.current.silhouette_uids) == set(uids)

    room = await orchestrator.set_consent(room_id=room.room_id, uid=uids[0], granted=True)
    assert room.preview.current.composed_uids == [uids[0]]
    assert room.preview.current.silhouette_uids == [uids[1]]


async def test_consent_revocation_removes_member_from_preview_and_storage(
    orchestrator, event, settings
):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    uid = uids[0]
    room = await orchestrator.set_consent(room_id=room.room_id, uid=uid, granted=True)
    room = await orchestrator.run_try_on(room_id=room.room_id, uid=uid)

    fitting_dir = Path(settings.storage_local_root) / room.room_id / "fittings" / uid
    assert fitting_dir.is_dir()

    room = await orchestrator.set_consent(room_id=room.room_id, uid=uid, granted=False)

    # 過去の合成からも即時除去され、画像実体も消えている（§7-1）
    assert uid not in room.preview.current.composed_uids
    assert uid in room.preview.current.silhouette_uids
    assert not fitting_dir.exists()

    logs = await orchestrator.repo.audits(room.room_id)
    revokes = [l for l in logs if l.action == AuditAction.consent_revoke]
    assert len(revokes) == 1
    assert revokes[0].detail["deleted_objects"] > 0


async def test_preview_revision_increments_and_keeps_history(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    first = room.preview.current.revision
    room = await orchestrator.set_consent(room_id=room.room_id, uid=uids[0], granted=True)
    assert room.preview.current.revision == first + 1
    assert len(room.preview.history) == first


async def test_try_on_generates_candidate_images(orchestrator, event, settings):
    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    room = await orchestrator.run_try_on(room_id=room.room_id, uid=uids[0])
    fitting = room.fittings[uids[0]]
    assert len(fitting.candidates) == 3
    for candidate in fitting.candidates:
        assert (Path(settings.storage_local_root) / candidate.image_ref).is_file()
    # 適合度の高い順に提示される
    scores = [c.tone_match for c in fitting.candidates]
    assert scores == sorted(scores, reverse=True)


async def test_alternatives_avoid_confirmed_colors_and_dress_code(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[1], garment_id="g_navy_lace"
    )
    alts = orchestrator.alternatives(room, uids[1])

    assert alts
    ids = {g.garment_id for g in alts}
    assert "g_navy_satin" not in ids  # 相手の確定色とかぶるものは出さない
    assert "g_ivory" not in ids  # 白は結婚式で block
    assert "g_fur_beige" not in ids  # ファーも block
    assert "g_casual_denim" not in ids  # フォーマル度が下限未満


async def test_reminders_target_only_pending_members(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子", "さくら"))
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    reminded = await orchestrator.remind(room.room_id)
    assert set(reminded) == set(uids[1:])

    # 通知は宛先本人ぶんだけ見える
    assert await orchestrator.notifications(room_id=room.room_id, uid=uids[0]) == []
    for uid in uids[1:]:
        got = await orchestrator.notifications(room_id=room.room_id, uid=uid)
        assert [n.kind.value for n in got] == ["pending"]


async def test_repeated_reminders_do_not_pile_up_while_unread(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    await orchestrator.remind(room.room_id)
    await orchestrator.remind(room.room_id)

    unread = await orchestrator.notifications(
        room_id=room.room_id, uid=uids[1], unread_only=True
    )
    assert len(unread) == 1  # 未読が残っているあいだは積み増さない

    # 既読にすれば、次の検知で改めて通知される
    assert await orchestrator.mark_read(room_id=room.room_id, uid=uids[1]) == 1
    await orchestrator.remind(room.room_id)
    assert len(
        await orchestrator.notifications(room_id=room.room_id, uid=uids[1], unread_only=True)
    ) == 1


async def test_harmony_warning_notifies_only_the_affected_members(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子", "さくら"))
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[1], garment_id="g_navy_lace"
    )
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[2], garment_id="g_terracotta"
    )

    # 色かぶりの当事者2人にだけ通知が出る
    for uid in uids[:2]:
        kinds = [
            n.kind.value
            for n in await orchestrator.notifications(room_id=room.room_id, uid=uid)
        ]
        assert "harmony" in kinds
    third = await orchestrator.notifications(room_id=room.room_id, uid=uids[2])
    assert "harmony" not in [n.kind.value for n in third]


async def test_mark_read_only_touches_own_notifications(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    await orchestrator.remind(room.room_id)

    await orchestrator.mark_read(room_id=room.room_id, uid=uids[0])
    others = await orchestrator.notifications(
        room_id=room.room_id, uid=uids[1], unread_only=True
    )
    assert others  # 他人の通知は既読にならない


async def test_purge_deletes_everything_but_keeps_audit_trail(orchestrator, event, settings):
    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    await orchestrator.run_try_on(room_id=room.room_id, uid=uids[0])

    result = await orchestrator.purge(room_id=room.room_id)
    assert result["deleted_objects"] > 0
    assert not (Path(settings.storage_local_root) / room.room_id).exists()

    with pytest.raises(Exception):
        await orchestrator.get_room(room.room_id)

    logs = await orchestrator.repo.audits(room.room_id)
    assert [l for l in logs if l.action == AuditAction.purge]


async def test_sweep_removes_only_expired_rooms(orchestrator, event, settings):
    live, _ = await setup_room(orchestrator, event, names=("幹事",))
    past = EventInfo(
        title="終了済み",
        event_date=date.today() - timedelta(days=settings.ttl_days_after_event + 1),
    )
    expired = await orchestrator.create_room(event=past, organizer_name="幹事")

    purged = await orchestrator.sweep_expired()
    assert len(purged) == 1
    assert await orchestrator.repo.get(expired.room_id) is None
    assert await orchestrator.repo.get(live.room_id) is not None


async def test_movie_requires_everyone_confirmed(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    await orchestrator.set_consent(room_id=room.room_id, uid=uids[0], granted=True)
    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )

    allowed, reason = orchestrator.movie_availability(room)
    assert allowed is False
    assert "全員" in reason
    with pytest.raises(ValueError):
        await orchestrator.create_movie(room.room_id)

    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[1], garment_id="g_bordeaux"
    )
    assert orchestrator.movie_availability(room)[0] is True

    room = await orchestrator.create_movie(room.room_id)
    assert room.movie is not None
    assert room.movie.engine == "local"
    assert room.movie.source_revision == room.preview.current.revision
    assert await orchestrator.storage.get(room.movie.movie_ref) is not None


async def test_movie_is_blocked_when_nobody_consented(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    room = await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    allowed, reason = orchestrator.movie_availability(room)
    assert allowed is False
    assert "同意" in reason


async def test_lighting_change_recomposes_the_preview(orchestrator, event):
    from app.domain.models import LightingPreset

    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    before = room.preview.current

    room = await orchestrator.set_lighting(
        room_id=room.room_id, lighting=LightingPreset.hall_evening
    )

    assert room.event.lighting == LightingPreset.hall_evening
    assert room.preview.current.revision == before.revision + 1
    assert room.preview.current.lighting == LightingPreset.hall_evening
    assert room.preview.current.engine == "local"


async def test_movie_is_purged_with_the_room(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事",))
    await orchestrator.set_consent(room_id=room.room_id, uid=uids[0], granted=True)
    await orchestrator.select_garment(
        room_id=room.room_id, uid=uids[0], garment_id="g_navy_satin"
    )
    room = await orchestrator.create_movie(room.room_id)
    ref = room.movie.movie_ref

    await orchestrator.purge(room_id=room.room_id)
    assert await orchestrator.storage.get(ref) is None


async def test_export_is_limited_to_own_images(orchestrator, event):
    room, uids = await setup_room(orchestrator, event, names=("幹事", "花子"))
    await orchestrator.run_try_on(room_id=room.room_id, uid=uids[0])
    await orchestrator.run_try_on(room_id=room.room_id, uid=uids[1])

    refs = await orchestrator.export_member(room_id=room.room_id, uid=uids[0])
    assert refs
    assert all(f"fittings/{uids[0]}/" in ref for ref in refs)
