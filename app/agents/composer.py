"""合成エージェント。

集合プレビューをリアルタイム更新する。合成は本人同意が前提で、
未同意・撤回済みのメンバーはシルエットで描く。撤回時は再合成により
過去の合成画像も差し替わり、旧リビジョンの実体はストレージから削除される。
"""

from __future__ import annotations

from app.domain.models import Preview, PreviewRevision, Room
from app.ports.compositor import CompositorPort
from app.ports.storage import StoragePort


class ComposerAgent:
    name = "composer-agent"

    def __init__(self, storage: StoragePort, compositor: CompositorPort) -> None:
        self.storage = storage
        self.compositor = compositor

    async def compose(self, room: Room, *, reason: str = "") -> Preview:
        figures: list[dict] = []
        composed: list[str] = []
        silhouettes: list[str] = []

        for member in room.active_members:
            fitting = room.fittings.get(member.uid)
            selected = fitting.selected if fitting else None
            if member.composable:
                composed.append(member.uid)
            else:
                silhouettes.append(member.uid)

            figures.append(
                {
                    "name": member.display_name,
                    "color_hex": selected.garment.primary_color_hex if selected else None,
                    "pattern": selected.garment.pattern.value if selected else "solid",
                    # 同意していない人は衣装が決まっていてもシルエットのまま
                    "silhouette": not member.composable,
                    "label": (
                        selected.garment.color_name
                        if selected and member.composable
                        else ("未同意" if not member.composable else "衣装未確定")
                    ),
                }
            )

        # 誰をシルエットにするかはここで決め切り、合成エンジンには判断を渡さない。
        png = await self.compositor.compose_group(
            figures=figures, lighting=room.event.lighting
        )
        revision = (room.preview.current.revision + 1) if room.preview.current else 1
        ref = await self.storage.put(
            room_id=room.room_id,
            key=f"preview/rev{revision}.png",
            data=png,
            content_type="image/png",
        )
        new_rev = PreviewRevision(
            revision=revision,
            image_ref=ref,
            composed_uids=composed,
            silhouette_uids=silhouettes,
            reason=reason,
            engine=self.compositor.engine,
            lighting=room.event.lighting,
        )

        history = [*room.preview.history]
        if room.preview.current:
            history.append(room.preview.current)
        return Preview(current=new_rev, history=history)

    async def forget_member(self, room: Room, *, uid: str) -> int:
        """同意撤回時に、そのメンバーの試着画像実体を削除する。

        過去の集合プレビューも合成し直しになるため、旧リビジョンの実体も消す。
        """
        deleted = await self.storage.delete_prefix(room_id=room.room_id, prefix=f"fittings/{uid}")
        for rev in room.preview.history + ([room.preview.current] if room.preview.current else []):
            if uid in rev.composed_uids:
                deleted += await self.storage.delete_prefix(
                    room_id=room.room_id, prefix=f"preview/rev{rev.revision}.png"
                )
        return deleted
