"""ルーム・監査ログの永続化ポート（メモリ / Firestore）。

Firestore の rooms/{roomId} と audit/{logId} に対応する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import AuditLog, Room


class RoomRepository(ABC):
    name = "repository"

    @abstractmethod
    async def save(self, room: Room) -> Room: ...

    @abstractmethod
    async def get(self, room_id: str) -> Room | None: ...

    @abstractmethod
    async def list_rooms(self) -> list[Room]: ...

    @abstractmethod
    async def delete(self, room_id: str) -> None: ...

    @abstractmethod
    async def append_audit(self, log: AuditLog) -> AuditLog: ...

    @abstractmethod
    async def audits(self, room_id: str) -> list[AuditLog]: ...


class MemoryRoomRepository(RoomRepository):
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._audits: list[AuditLog] = []

    async def save(self, room: Room) -> Room:
        # 参照共有による意図しない書き換えを避けるためコピーを保持する。
        self._rooms[room.room_id] = room.model_copy(deep=True)
        return room

    async def get(self, room_id: str) -> Room | None:
        room = self._rooms.get(room_id)
        return room.model_copy(deep=True) if room else None

    async def list_rooms(self) -> list[Room]:
        return [r.model_copy(deep=True) for r in self._rooms.values()]

    async def delete(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    async def append_audit(self, log: AuditLog) -> AuditLog:
        self._audits.append(log)
        return log

    async def audits(self, room_id: str) -> list[AuditLog]:
        # 監査ログはルーム削除後も残す（削除の証跡そのものが必要なため）。
        return [a for a in self._audits if a.room_id == room_id]


class FirestoreRoomRepository(RoomRepository):
    """既定の実装。

    ローカルでは Firestore エミュレータ（compose の firestore サービス）に、
    本番では Cloud Run のサービスアカウントで実際の Firestore に接続する。
    接続先は環境変数 FIRESTORE_EMULATOR_HOST の有無だけで決まり、コードは同じ。

    ルームは1ドキュメントに丸ごと入れる。ドキュメント上限は 1MiB だが、
    画像は Cloud Storage に置いて参照だけを持つため収まる。
    """

    def __init__(self, project: str) -> None:
        from google.cloud import firestore

        self._db = firestore.AsyncClient(project=project)

    def _doc(self, room_id: str):
        return self._db.collection("rooms").document(room_id)

    async def save(self, room: Room) -> Room:
        # mode="json" で datetime/date/Enum を Firestore が扱える素の型に落とす
        await self._doc(room.room_id).set(room.model_dump(mode="json"))
        return room

    async def get(self, room_id: str) -> Room | None:
        snap = await self._doc(room_id).get()
        return Room.model_validate(snap.to_dict()) if snap.exists else None

    async def list_rooms(self) -> list[Room]:
        return [
            Room.model_validate(d.to_dict())
            async for d in self._db.collection("rooms").stream()
        ]

    async def delete(self, room_id: str) -> None:
        await self._doc(room_id).delete()

    async def append_audit(self, log: AuditLog) -> AuditLog:
        await self._db.collection("audit").document(log.log_id).set(
            log.model_dump(mode="json")
        )
        return log

    async def audits(self, room_id: str) -> list[AuditLog]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._db.collection("audit").where(
            filter=FieldFilter("room_id", "==", room_id)
        )
        logs = [AuditLog.model_validate(d.to_dict()) async for d in query.stream()]
        # 監査ログは時系列で読む。複合インデックスを要求しないようアプリ側で並べる。
        return sorted(logs, key=lambda log: log.created_at)
