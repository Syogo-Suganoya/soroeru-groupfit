"""API のリクエスト / レスポンススキーマ。

レスポンスでは「誰の顔が合成されているか」を明示し、同意状態を常に返す（§7-3）。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.domain.models import (
    ArrangePlan,
    Garment,
    HarmonyReport,
    LightingPreset,
    Movie,
    PreviewRevision,
    Room,
    SceneType,
)


class CreateRoomRequest(BaseModel):
    title: str = "お呼ばれ"
    scene: SceneType = SceneType.wedding
    event_date: date
    dress_code: str | None = None
    organizer_name: str
    lighting: LightingPreset = LightingPreset.none


class JoinRequest(BaseModel):
    display_name: str


class ConsentRequest(BaseModel):
    granted: bool


class TryOnRequest(BaseModel):
    garment_ids: list[str] | None = None


class SelectRequest(BaseModel):
    garment_id: str


class LightingRequest(BaseModel):
    lighting: LightingPreset


class ReadNotificationsRequest(BaseModel):
    # 省略時は本人ぶんの未読をすべて既読にする
    notification_ids: list[str] | None = None


class MemberView(BaseModel):
    uid: str
    display_name: str
    is_organizer: bool
    state: str
    consent_granted: bool
    composed_in_preview: bool  # False ならプレビューではシルエット
    unread_notifications: int = 0
    selected_garment: Garment | None = None
    candidates: list[Garment] = Field(default_factory=list)


class RoomView(BaseModel):
    room_id: str
    status: str
    title: str
    scene: str
    event_date: date
    dress_code: str | None
    invite_url: str
    ttl_at: str
    members: list[MemberView]
    harmony: HarmonyReport
    preview: PreviewRevision | None
    preview_url: str | None
    arrange: ArrangePlan | None
    all_confirmed: bool
    lighting: LightingPreset
    lighting_label: str
    movie: Movie | None = None
    movie_url: str | None = None
    movie_available: bool = False  # 記念ムービーを作れる状態か
    movie_blocked_reason: str = ""

    @classmethod
    def of(
        cls,
        room: Room,
        *,
        base_url: str,
        ttl_days: int,
        movie_availability: tuple[bool, str] = (False, ""),
    ) -> "RoomView":
        composed = set(room.preview.current.composed_uids) if room.preview.current else set()
        unread = room.unread_counts()
        members: list[MemberView] = []
        for m in room.members:
            fitting = room.fittings.get(m.uid)
            members.append(
                MemberView(
                    uid=m.uid,
                    display_name=m.display_name,
                    is_organizer=m.is_organizer,
                    state=m.state.value,
                    consent_granted=m.consent.granted,
                    composed_in_preview=m.uid in composed,
                    unread_notifications=unread.get(m.uid, 0),
                    selected_garment=fitting.selected.garment if fitting and fitting.selected else None,
                    candidates=[c.garment for c in fitting.candidates] if fitting else [],
                )
            )
        return cls(
            room_id=room.room_id,
            status=room.status.value,
            title=room.event.title,
            scene=room.event.scene.value,
            event_date=room.event.event_date,
            dress_code=room.event.dress_code,
            invite_url=f"{base_url}/?room={room.room_id}",
            ttl_at=room.ttl_at(ttl_days).isoformat(),
            members=members,
            harmony=room.harmony,
            preview=room.preview.current,
            preview_url=(
                f"{base_url}/api/rooms/{room.room_id}/preview.png?rev={room.preview.current.revision}"
                if room.preview.current
                else None
            ),
            arrange=room.arrange,
            all_confirmed=room.all_confirmed,
            lighting=room.event.lighting,
            lighting_label=room.event.lighting.label,
            movie=room.movie,
            movie_url=(
                f"{base_url}/api/rooms/{room.room_id}/movie" if room.movie else None
            ),
            movie_available=movie_availability[0],
            movie_blocked_reason=movie_availability[1],
        )
