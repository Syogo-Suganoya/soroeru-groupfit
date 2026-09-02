"""手配エージェント（設計書 §4）。

確定後のレンタル・購入リンク接続と、準備進捗の見守り・リマインド。
リマインドは自律だが、購入・予約そのものは行わない（リンク提示まで）。

通知はルーム内に積み、PWA が取りに来る（アプリ内通知）。外部配信が要るときだけ
MessagingPort の実装を差し替える。
"""

from __future__ import annotations

from datetime import date

from app.domain import harmony as rules
from app.domain.models import (
    ArrangeItem,
    ArrangePlan,
    Notification,
    NotificationKind,
    Room,
)
from app.ports.messaging import MessagingPort


class ArrangerAgent:
    name = "arranger-agent"

    def __init__(self, messaging: MessagingPort) -> None:
        self.messaging = messaging

    async def _notify(
        self, room: Room, *, uid: str, kind: NotificationKind, text: str
    ) -> Notification | None:
        """通知を1件積む。

        advance() は状態が動くたびに検知をやり直すため、同じ内容の未読が残っている
        あいだは積み増さない。既読にしたあと再び検知されれば、改めて通知される。
        """
        candidate = Notification(to_uid=uid, kind=kind, text=text)
        for existing in room.notifications:
            if not existing.is_read and existing.dedupe_key == candidate.dedupe_key:
                return None

        room.notifications.append(candidate)
        await self.messaging.push(room_id=room.room_id, notification=candidate)
        return candidate

    def build_plan(self, room: Room) -> ArrangePlan:
        items: list[ArrangeItem] = []
        for uid, fitting in room.confirmed_fittings().items():
            garment = fitting.selected.garment  # type: ignore[union-attr]
            items.append(
                ArrangeItem(
                    member_uid=uid,
                    garment_name=garment.name,
                    rental_url=garment.rental_url,
                    price_yen=garment.price_yen,
                )
            )

        days_left = (room.event.event_date - date.today()).days
        reminders = [
            f"{room.event.title}まであと{days_left}日です。レンタルの予約期限にご注意ください。"
            if days_left >= 0
            else f"{room.event.title}は終了しました。ルームは自動削除されます。"
        ]
        return ArrangePlan(items=items, reminders=reminders)

    async def remind_pending(self, room: Room) -> list[str]:
        """未確定メンバーへの催促。全員確定するまで自律的に繰り返す対象。"""
        sent: list[str] = []
        for member in rules.pending_members(room):
            notification = await self._notify(
                room,
                uid=member.uid,
                kind=NotificationKind.pending,
                text=(
                    f"{member.display_name}さん、{room.event.title}の衣装がまだ未確定です。"
                    "集合プレビューは全員分そろってから最終確認できます。"
                ),
            )
            if notification:
                sent.append(member.uid)
        return sent

    async def notify_warnings(self, room: Room, uids: list[str]) -> list[str]:
        sent: list[str] = []
        for uid in uids:
            member = room.member(uid)
            if not member:
                continue
            related = [w for w in room.harmony.warnings if uid in w.member_uids]
            if not related:
                continue
            body = "\n".join(f"・{w.message}" for w in related)
            notification = await self._notify(
                room,
                uid=uid,
                kind=NotificationKind.harmony,
                text=f"集合プレビューの確認結果です。\n{body}\n代替案をアプリで確認できます。",
            )
            if notification:
                sent.append(uid)
        return sent

    async def announce_ready(self, room: Room) -> list[str]:
        sent: list[str] = []
        for member in room.active_members:
            notification = await self._notify(
                room,
                uid=member.uid,
                kind=NotificationKind.ready,
                text=(
                    f"全員の衣装が確定しました。{room.event.title}の集合プレビューを"
                    "アプリで確認できます。"
                ),
            )
            if notification:
                sent.append(member.uid)
        return sent
