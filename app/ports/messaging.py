"""通知の外部配信ポート。

アプリ内通知（既定）では通知そのものが Room に保持されるため、外部配信は行わない。
LINE などに広げたくなったときだけ、このポートの実装を差し替える。
配信チャネルを変えてもエージェント側のコードは変わらない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Notification, now


class MessagingPort(ABC):
    name = "messaging"

    @abstractmethod
    async def push(self, *, room_id: str, notification: Notification) -> None:
        """アプリ外へ届ける。アプリ内で完結する場合は何もしない。"""


class InAppMessagingPort(MessagingPort):
    """既定。通知は Room に積まれ、PWA が取りに来る（外部送信なし）。"""

    name = "in_app"

    async def push(self, *, room_id: str, notification: Notification) -> None:
        return None


class MockMessagingPort(MessagingPort):
    """テスト・デモ用。外部配信の呼び出しを記録するだけ。"""

    name = "mock"

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    async def push(self, *, room_id: str, notification: Notification) -> None:
        self.outbox.append(
            {
                "room_id": room_id,
                "to": notification.to_uid,
                "kind": notification.kind.value,
                "text": notification.text,
                "sent_at": now().isoformat(),
            }
        )

    def messages_for(self, room_id: str) -> list[dict]:
        return [m for m in self.outbox if m["room_id"] == room_id]


class LineMessagingPort(MessagingPort):
    """NOTIFY_CHANNEL=line のときのみ使用。未検証（実キーでの疎通は未確認）。"""

    name = "line"

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    async def push(self, *, room_id: str, notification: Notification) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "to": notification.to_uid,
                    "messages": [{"type": "text", "text": notification.text}],
                },
            )
