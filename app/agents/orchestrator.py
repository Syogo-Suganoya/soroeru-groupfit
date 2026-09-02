"""Orchestrator（設計書 §4）。

ルーム状態が変わるたびに `advance()` が走り、調和判定 → 集合プレビュー再合成 →
（必要なら）通知・手配、までを自律的に進める。API 層はイベントを渡すだけで、
進行の判断はここに閉じている。

同意・撤回・削除は必ず監査ログを残す（§7-4）。
"""

from __future__ import annotations

from datetime import datetime

from app.agents.arranger import ArrangerAgent
from app.agents.composer import ComposerAgent
from app.agents.fitting import FittingAgent
from app.agents.harmony_agent import HarmonyAgent
from app.agents.keepsake import KeepsakeAgent
from app.config import Settings
from app.domain import catalog
from app.domain.models import (
    AuditAction,
    AuditLog,
    EventInfo,
    Fitting,
    Garment,
    LightingPreset,
    Member,
    MemberState,
    Movie,
    Notification,
    Room,
    RoomStatus,
    now,
)
from app.ports.compositor import CompositorPort
from app.ports.llm import LlmPort
from app.ports.messaging import MessagingPort
from app.ports.repository import RoomRepository
from app.ports.storage import StoragePort
from app.ports.tryon import TryOnPort
from app.ports.video import VideoPort


class RoomNotFound(Exception):
    pass


class MemberNotFound(Exception):
    pass


class Orchestrator:
    name = "orchestrator"

    def __init__(
        self,
        *,
        settings: Settings,
        repo: RoomRepository,
        storage: StoragePort,
        tryon: TryOnPort,
        llm: LlmPort,
        messaging: MessagingPort,
        compositor: CompositorPort,
        video: VideoPort,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.storage = storage
        self.messaging = messaging
        self.fitting_agent = FittingAgent(tryon)
        self.composer = ComposerAgent(storage, compositor)
        self.keepsake = KeepsakeAgent(storage, video)
        self.harmony_agent = HarmonyAgent(
            llm,
            color_clash_delta_e=settings.color_clash_delta_e,
            formality_gap_threshold=settings.formality_gap_threshold,
        )
        self.arranger = ArrangerAgent(messaging)

    # ------------------------------------------------------------- 監査

    async def audit(
        self,
        *,
        room_id: str | None,
        actor: str,
        action: AuditAction,
        target: str | None = None,
        detail: dict | None = None,
    ) -> AuditLog:
        return await self.repo.append_audit(
            AuditLog(
                room_id=room_id,
                actor=actor,
                action=action,
                target=target,
                detail=detail or {},
                policy=self.settings.consent_policy,
            )
        )

    # ------------------------------------------------------------- 取得

    async def get_room(self, room_id: str) -> Room:
        room = await self.repo.get(room_id)
        if room is None:
            raise RoomNotFound(room_id)
        return room

    def _member(self, room: Room, uid: str) -> Member:
        member = room.member(uid)
        if member is None:
            raise MemberNotFound(uid)
        return member

    # ------------------------------------------------------------- ルーム進行

    async def create_room(self, *, event: EventInfo, organizer_name: str) -> Room:
        organizer = Member(
            display_name=organizer_name, is_organizer=True, state=MemberState.joined
        )
        room = Room(event=event, members=[organizer])
        await self.repo.save(room)
        await self.audit(
            room_id=room.room_id,
            actor=organizer.uid,
            action=AuditAction.room_create,
            detail={
                "scene": event.scene.value,
                "event_date": event.event_date.isoformat(),
                "ttl_at": room.ttl_at(self.settings.ttl_days_after_event).isoformat(),
            },
        )
        return await self.advance(room, reason="ルーム作成")

    async def join(self, *, room_id: str, display_name: str) -> tuple[Room, Member]:
        room = await self.get_room(room_id)
        member = Member(display_name=display_name, state=MemberState.joined)
        room.members.append(member)
        await self.audit(
            room_id=room.room_id,
            actor=member.uid,
            action=AuditAction.member_join,
            detail={"display_name": display_name},
        )
        room = await self.advance(room, reason=f"{display_name}さんが参加")
        return room, member

    async def set_consent(self, *, room_id: str, uid: str, granted: bool) -> Room:
        """§7-1: 合成同意の取得・撤回。撤回時は画像実体まで消してから再合成する。"""
        room = await self.get_room(room_id)
        member = self._member(room, uid)
        member.consent = member.consent.grant() if granted else member.consent.revoke()

        deleted = 0
        if not granted:
            deleted = await self.composer.forget_member(room, uid=uid)

        await self.audit(
            room_id=room.room_id,
            actor=uid,
            action=AuditAction.consent_grant if granted else AuditAction.consent_revoke,
            target=uid,
            detail={
                "display_name": member.display_name,
                "deleted_objects": deleted,
                "at": now().isoformat(),
            },
        )
        return await self.advance(
            room,
            reason=f"{member.display_name}さんが合成同意を{'許可' if granted else '撤回'}",
        )

    async def run_try_on(
        self, *, room_id: str, uid: str, garment_ids: list[str] | None = None
    ) -> Room:
        room = await self.get_room(room_id)
        member = self._member(room, uid)

        if garment_ids:
            garments: list[Garment] = [
                g for g in (catalog.find(gid) for gid in garment_ids) if g is not None
            ]
        else:
            taken = {
                f.selected_garment_id
                for other, f in room.confirmed_fittings().items()
                if other != uid and f.selected_garment_id
            }
            garments = self.fitting_agent.suggest_garments(exclude_ids=taken)  # type: ignore[arg-type]

        if not garments:
            raise ValueError("試着対象の衣装がありません")

        fitting = await self.fitting_agent.run(
            room_id=room.room_id, member=member, garments=garments
        )
        # すでに確定済みなら選択は維持する（再試着で確定が消えないように）
        previous = room.fittings.get(uid)
        if previous and previous.selected_garment_id:
            known = {c.garment.garment_id for c in fitting.candidates}
            if previous.selected_garment_id in known:
                fitting.selected_garment_id = previous.selected_garment_id
            elif previous.selected:
                fitting.candidates.append(previous.selected)
                fitting.selected_garment_id = previous.selected_garment_id

        room.fittings[uid] = fitting
        if member.state == MemberState.joined:
            member.state = MemberState.fitting

        await self.audit(
            room_id=room.room_id,
            actor=self.fitting_agent.name,
            action=AuditAction.tryon,
            target=uid,
            detail={"garments": [g.garment_id for g in garments]},
        )
        return await self.advance(room, reason=f"{member.display_name}さんの試着")

    async def select_garment(self, *, room_id: str, uid: str, garment_id: str) -> Room:
        room = await self.get_room(room_id)
        member = self._member(room, uid)
        fitting = room.fittings.get(uid)

        if fitting is None or garment_id not in {
            c.garment.garment_id for c in fitting.candidates
        }:
            # 未試着の衣装を直接選んだ場合は、その場で試着してから確定する
            room = await self.run_try_on(room_id=room_id, uid=uid, garment_ids=[garment_id])
            fitting = room.fittings[uid]
            member = self._member(room, uid)

        fitting.selected_garment_id = garment_id
        fitting.updated_at = now()
        member.state = MemberState.confirmed

        await self.audit(
            room_id=room.room_id,
            actor=uid,
            action=AuditAction.garment_select,
            target=garment_id,
            detail={"display_name": member.display_name},
        )
        return await self.advance(room, reason=f"{member.display_name}さんが衣装確定")

    # ------------------------------------------------------------- 自律進行

    async def advance(self, room: Room, *, reason: str = "") -> Room:
        """ルーム状態駆動の自律進行（設計書 §4 Orchestrator）。

        1. 調和判定（確定衣装がある人だけ）
        2. 集合プレビューを再合成（同意者のみ顔合成）
        3. 全員確定なら手配プラン + 全体通知、未確定がいれば催促
        """
        room.harmony = await self.harmony_agent.evaluate(room)
        await self.audit(
            room_id=room.room_id,
            actor=self.harmony_agent.name,
            action=AuditAction.harmony_evaluate,
            detail={
                "warnings": len(room.harmony.warnings),
                "formality_median": room.harmony.formality_median,
            },
        )

        room.preview = await self.composer.compose(room, reason=reason)
        await self.audit(
            room_id=room.room_id,
            actor=self.composer.name,
            action=AuditAction.preview_compose,
            target=room.preview.current.image_ref if room.preview.current else None,
            detail={
                "composed": room.preview.current.composed_uids if room.preview.current else [],
                "silhouette": (
                    room.preview.current.silhouette_uids if room.preview.current else []
                ),
                "reason": reason,
            },
        )

        if room.all_confirmed:
            if room.status != RoomStatus.arranged:
                notified = await self.arranger.announce_ready(room)
                if notified:
                    await self.audit(
                        room_id=room.room_id,
                        actor=self.arranger.name,
                        action=AuditAction.reminder,
                        detail={"kind": "ready", "to": notified},
                    )
            room.arrange = self.arranger.build_plan(room)
            room.status = RoomStatus.arranged
            await self.audit(
                room_id=room.room_id,
                actor=self.arranger.name,
                action=AuditAction.arrange,
                detail={"items": len(room.arrange.items)},
            )
        else:
            room.status = RoomStatus.open

        warned = self.harmony_agent.warned_uids(room.harmony)
        if warned:
            notified = await self.arranger.notify_warnings(room, warned)
            if notified:
                await self.audit(
                    room_id=room.room_id,
                    actor=self.arranger.name,
                    action=AuditAction.reminder,
                    detail={"kind": "harmony", "to": notified},
                )

        room.updated_at = now()
        await self.repo.save(room)
        return room

    async def remind(self, room_id: str) -> list[str]:
        room = await self.get_room(room_id)
        sent = await self.arranger.remind_pending(room)
        if sent:
            await self.audit(
                room_id=room_id,
                actor=self.arranger.name,
                action=AuditAction.reminder,
                detail={"kind": "pending", "to": sent},
            )
        await self.repo.save(room)
        return sent

    # ------------------------------------------------------------- 通知

    async def notifications(
        self, *, room_id: str, uid: str, unread_only: bool = False
    ) -> list[Notification]:
        room = await self.get_room(room_id)
        self._member(room, uid)
        return room.notifications_for(uid, unread_only=unread_only)

    async def mark_read(
        self, *, room_id: str, uid: str, notification_ids: list[str] | None = None
    ) -> int:
        """既読化。宛先本人ぶんしか触れない。"""
        room = await self.get_room(room_id)
        self._member(room, uid)

        targets = set(notification_ids) if notification_ids else None
        count = 0
        for notification in room.notifications:
            if notification.to_uid != uid or notification.is_read:
                continue
            if targets is not None and notification.notification_id not in targets:
                continue
            notification.read_at = now()
            count += 1

        if count:
            room.updated_at = now()
            await self.repo.save(room)
        return count

    # ------------------------------------------------------------- ライティング・記念

    async def set_lighting(self, *, room_id: str, lighting: LightingPreset) -> Room:
        """会場の光環境を設定し、集合プレビューを作り直す（設計書 §12）。"""
        room = await self.get_room(room_id)
        room.event.lighting = lighting
        return await self.advance(room, reason=f"ライティングを{lighting.label}に変更")

    async def create_movie(self, room_id: str) -> Room:
        """記念ムービーを作る。重い処理なので依頼されたときだけ動く。"""
        room = await self.get_room(room_id)
        room.movie = await self.keepsake.create(room)
        await self.audit(
            room_id=room_id,
            actor=self.keepsake.name,
            action=AuditAction.movie_create,
            target=room.movie.movie_ref,
            detail={
                "engine": room.movie.engine,
                "seconds": room.movie.seconds,
                "source_revision": room.movie.source_revision,
                # 記念ムービーに写るのは合成に同意している人だけ
                "composed": room.preview.current.composed_uids if room.preview.current else [],
            },
        )
        room.updated_at = now()
        await self.repo.save(room)
        return room

    def movie_availability(self, room: Room) -> tuple[bool, str]:
        return self.keepsake.can_create(room)

    # ------------------------------------------------------------- 提案・出力

    def alternatives(self, room: Room, uid: str) -> list[Garment]:
        return self.harmony_agent.alternatives(room, uid=uid)

    async def export_member(self, *, room_id: str, uid: str) -> list[str]:
        """§7-2: エクスポートは本人の分のみ。集合プレビューは含めない。"""
        room = await self.get_room(room_id)
        fitting = room.fittings.get(uid)
        refs = [c.image_ref for c in fitting.candidates] if fitting else []
        await self.audit(
            room_id=room_id,
            actor=uid,
            action=AuditAction.export,
            target=uid,
            detail={"count": len(refs), "scope": "self_only"},
        )
        return refs

    # ------------------------------------------------------------- 消去

    async def purge(self, *, room_id: str, actor: str = "system", reason: str = "manual") -> dict:
        """§7-2: ルーム単位の完全消去。監査ログだけは削除の証跡として残す。"""
        room = await self.get_room(room_id)
        deleted = await self.storage.delete_prefix(room_id=room_id)
        await self.repo.delete(room_id)
        detail = {
            "reason": reason,
            "deleted_objects": deleted,
            "members": len(room.members),
            "purged_at": now().isoformat(),
        }
        await self.audit(
            room_id=room_id, actor=actor, action=AuditAction.purge, detail=detail
        )
        return detail

    async def sweep_expired(self, *, at: datetime | None = None) -> list[dict]:
        """TTL 到来ルームの自動削除。Cloud Scheduler から叩く想定。"""
        results: list[dict] = []
        for room in await self.repo.list_rooms():
            if room.is_expired(self.settings.ttl_days_after_event, at):
                results.append(
                    await self.purge(room_id=room.room_id, reason="ttl_expired")
                )
        return results


__all__ = ["Orchestrator", "RoomNotFound", "MemberNotFound", "Fitting"]
