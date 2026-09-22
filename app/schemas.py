"""API のリクエスト / レスポンススキーマ。

レスポンスでは「誰の顔が合成されているか」を明示し、同意状態を常に返す。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.domain.dresscode import NG_LABELS, NG_RULES, preset_for
from app.domain.models import (
    ArrangePlan,
    Garment,
    HarmonyReport,
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


class JoinRequest(BaseModel):
    display_name: str


class ConsentRequest(BaseModel):
    granted: bool


class TryOnRequest(BaseModel):
    garment_ids: list[str] | None = None


class SelectRequest(BaseModel):
    garment_id: str


class ReadNotificationsRequest(BaseModel):
    # 省略時は本人ぶんの未読をすべて既読にする
    notification_ids: list[str] | None = None


class CandidateView(BaseModel):
    """試着候補1件。画像URLを含むため、本人だけが取得できる場所に置く。"""

    garment: Garment
    image_url: str
    tone_match: float = 0.0
    note: str | None = None
    selected: bool = False


class FittingView(BaseModel):
    """本人の試着結果。ルーム全体のビューには含めない。"""

    member_uid: str
    candidates: list[CandidateView] = Field(default_factory=list)
    selected_garment_id: str | None = None

    @classmethod
    def of(cls, room: Room, uid: str, *, base_url: str) -> "FittingView":
        fitting = room.fittings.get(uid)
        if fitting is None:
            return cls(member_uid=uid)
        return cls(
            member_uid=uid,
            selected_garment_id=fitting.selected_garment_id,
            candidates=[
                CandidateView(
                    garment=c.garment,
                    image_url=f"{base_url}/api/images/{c.image_ref}",
                    tone_match=c.tone_match,
                    note=c.note,
                    selected=c.garment.garment_id == fitting.selected_garment_id,
                )
                for c in fitting.candidates
            ],
        )


class MemberView(BaseModel):
    uid: str
    display_name: str
    is_organizer: bool
    state: str
    consent_granted: bool
    composed_in_preview: bool  # False ならプレビューではシルエット
    unread_notifications: int = 0
    # 確定衣装は集合プレビューに現れるため全員に見せる。試着の途中経過は見せない。
    selected_garment: Garment | None = None


class NgRuleView(BaseModel):
    """判定に使っている NG。どこから来たか（シーンの基本 / ドレスコードの指定）を添える。"""

    rule: str
    label: str
    by_scene: bool
    by_dress_code: bool


class RoomView(BaseModel):
    room_id: str
    status: str
    title: str
    scene: str
    event_date: date
    dress_code: str | None
    scene_label: str
    ng_rules: list[NgRuleView]
    dress_rules_by: str | None  # ドレスコードを読んだエンジン（gemini | stub）。記述なしなら None
    invite_url: str
    ttl_at: str
    members: list[MemberView]
    harmony: HarmonyReport
    preview: PreviewRevision | None
    preview_url: str | None
    arrange: ArrangePlan | None
    all_confirmed: bool

    @classmethod
    def of(cls, room: Room, *, base_url: str, ttl_days: int) -> "RoomView":
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
                )
            )
        return cls(
            room_id=room.room_id,
            status=room.status.value,
            title=room.event.title,
            scene=room.event.scene.value,
            event_date=room.event.event_date,
            dress_code=room.event.dress_code,
            scene_label=preset_for(room.event.scene).label,
            ng_rules=_ng_rules(room),
            dress_rules_by=room.event.dress_rules.interpreted_by if room.event.dress_rules else None,
            invite_url=f"{base_url}/app?room={room.room_id}",
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
        )


def _ng_rules(room: Room) -> list[NgRuleView]:
    base = preset_for(room.event.scene)
    read = room.event.dress_rules
    out: list[NgRuleView] = []
    for rule in NG_RULES:
        by_scene = bool(getattr(base, f"ng_{rule}"))
        by_dress_code = bool(read and getattr(read, f"ng_{rule}"))
        if by_scene or by_dress_code:
            out.append(
                NgRuleView(
                    rule=rule, label=NG_LABELS[rule], by_scene=by_scene, by_dress_code=by_dress_code
                )
            )
    return out
