"""記念エージェント。

全員の衣装が確定したあと、集合プレビューから短い記念ムービーを作る。
合意形成が終わったことを祝うための機能で、判定や手配には影響しない。

自律性は「作らない」側に倒してある。生成は重く課金もかかるため、
依頼されたときだけ作る（勝手に外部へ投げない）。
"""

from __future__ import annotations

from app.domain.models import Movie, Room
from app.ports.storage import StoragePort
from app.ports.video import VideoPort


class KeepsakeAgent:
    name = "keepsake-agent"

    def __init__(self, storage: StoragePort, video: VideoPort) -> None:
        self.storage = storage
        self.video = video

    @staticmethod
    def can_create(room: Room) -> tuple[bool, str]:
        """作ってよい状態かを、理由つきで返す。"""
        if room.preview.current is None:
            return False, "集合プレビューがまだありません。"
        if not room.all_confirmed:
            return False, "全員の衣装が確定してから作成できます。"
        if not room.composable_members:
            return False, "合成に同意しているメンバーがいません。"
        return True, ""

    def _prompt(self, room: Room) -> str:
        return (
            f"A gentle celebratory group photo moment for {room.event.title}, "
            "subtle camera push-in, soft natural motion, warm and calm"
        )

    async def create(self, room: Room) -> Movie:
        """集合プレビューから記念ムービーを作り、ルーム内ストレージに保存する。"""
        allowed, reason = self.can_create(room)
        if not allowed:
            raise ValueError(reason)

        revision = room.preview.current  # can_create で None でないことを確認済み
        assert revision is not None
        source = await self.storage.get(revision.image_ref)
        if source is None:
            raise ValueError("集合プレビューの画像が見つかりません。")

        result = await self.video.from_image(image=source, prompt=self._prompt(room))
        extension = "mp4" if result.content_type == "video/mp4" else "gif"
        ref = await self.storage.put(
            room_id=room.room_id,
            key=f"movie/rev{revision.revision}.{extension}",
            data=result.data,
            content_type=result.content_type,
        )
        return Movie(
            movie_ref=ref,
            content_type=result.content_type,
            seconds=result.seconds,
            engine=result.engine,
            has_audio=result.has_audio,
            source_revision=revision.revision,
        )
