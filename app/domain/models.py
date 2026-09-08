"""設計書 §6 のデータモデル（Firestore）。

中核の不変条件は §7-1「同意ベースの合成」。
集合プレビューに顔を合成してよいのは consent.granted が True のメンバーだけで、
撤回されたら過去の合成からも即時に消える。この判定は Member.composable に集約し、
合成エージェント側では再実装しない（型と1メソッドで担保する）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# --------------------------------------------------------------------------- イベント


class SceneType(str, Enum):
    """シーンプリセット。MVP は wedding のみ実装（設計書 §9）。"""

    wedding = "wedding"
    coming_of_age = "coming_of_age"  # 成人式（MVP外）
    cosplay = "cosplay"  # コスプレ合わせ（MVP外）

    @property
    def label(self) -> str:
        return {
            SceneType.wedding: "結婚式お呼ばれ",
            SceneType.coming_of_age: "成人式",
            SceneType.cosplay: "コスプレ合わせ",
        }[self]


class LightingPreset(str, Enum):
    """会場のライト環境（設計書 §12 会場ライティング再現）。

    集合プレビューを式場の光に寄せて、当日の見え方に近づけるための指定。
    ローカル合成では色調の調整で近似する。
    """

    none = "none"
    garden_day = "garden_day"
    hall_evening = "hall_evening"
    chapel = "chapel"

    @property
    def label(self) -> str:
        return {
            LightingPreset.none: "指定なし",
            LightingPreset.garden_day: "昼のガーデン",
            LightingPreset.hall_evening: "ホテル宴会場（夜）",
            LightingPreset.chapel: "チャペル",
        }[self]

    @property
    def prompt(self) -> str:
        """relight モデルに渡す光環境の説明。"""
        return {
            LightingPreset.none: "",
            LightingPreset.garden_day: "bright outdoor daylight, soft sunlight, garden venue",
            LightingPreset.hall_evening: "warm indoor chandelier light, evening banquet hall",
            LightingPreset.chapel: "soft diffused window light, bright chapel interior",
        }[self]


class EventInfo(BaseModel):
    """rooms/{roomId}.event — 種別・日時・ドレスコード。"""

    scene: SceneType = SceneType.wedding
    title: str = "お呼ばれ"
    event_date: date
    dress_code: str | None = None  # 自由記述。Gemini がプリセットへ解釈する。
    lighting: LightingPreset = LightingPreset.none


# --------------------------------------------------------------------------- 同意


class Consent(BaseModel):
    """§7-1: 合成同意。撤回時刻も残し、監査ログと突き合わせられるようにする。"""

    granted: bool = False
    granted_at: datetime | None = None
    revoked_at: datetime | None = None

    def grant(self) -> "Consent":
        return Consent(granted=True, granted_at=now(), revoked_at=None)

    def revoke(self) -> "Consent":
        return Consent(granted=False, granted_at=self.granted_at, revoked_at=now())


class MemberState(str, Enum):
    invited = "invited"
    joined = "joined"
    fitting = "fitting"  # 試着候補を生成した
    confirmed = "confirmed"  # 衣装を確定した
    left = "left"


class Member(BaseModel):
    uid: str = Field(default_factory=lambda: new_id("uid"))
    display_name: str
    is_organizer: bool = False
    consent: Consent = Field(default_factory=Consent)
    state: MemberState = MemberState.invited
    photo_ref: str | None = None  # ルーム内一時ストレージの参照

    @property
    def composable(self) -> bool:
        """集合プレビューに顔を合成してよいか。未同意はシルエット表示になる。"""
        return self.consent.granted and self.state != MemberState.left


# --------------------------------------------------------------------------- 衣装・試着


class PatternFamily(str, Enum):
    solid = "solid"
    floral = "floral"  # 花柄
    geometric = "geometric"  # 幾何・ストライプ
    lace = "lace"
    animal = "animal"


class Garment(BaseModel):
    """カタログ上の1着。主要色とフォーマル度はここを一次情報とする。"""

    garment_id: str
    name: str
    category: str  # ドレス / 振袖 / セットアップ …
    primary_color_hex: str
    color_name: str
    pattern: PatternFamily = PatternFamily.solid
    formality: int = 3  # 1(カジュアル) - 5(フォーマル)
    has_fur: bool = False
    price_yen: int = 0
    rental_url: str | None = None


class TryOnResult(BaseModel):
    """YouCam AI Clothes Try-On の結果1件。"""

    garment: Garment
    image_ref: str  # ルーム内ストレージ参照（本人以外にはプレビュー経由でのみ露出）
    tone_match: float = 0.0  # Facial Color Tones Analyzer 由来のパーソナルカラー適合度
    note: str | None = None


class Fitting(BaseModel):
    """rooms/{roomId}/fittings/{memberId}。"""

    member_uid: str
    candidates: list[TryOnResult] = Field(default_factory=list)
    selected_garment_id: str | None = None
    updated_at: datetime = Field(default_factory=now)

    @property
    def selected(self) -> TryOnResult | None:
        if not self.selected_garment_id:
            return None
        for c in self.candidates:
            if c.garment.garment_id == self.selected_garment_id:
                return c
        return None


# --------------------------------------------------------------------------- 調和判定


class WarningKind(str, Enum):
    color_clash = "color_clash"  # 色かぶり
    pattern_clash = "pattern_clash"  # 柄かぶり
    formality_outlier = "formality_outlier"  # 1人だけ浮く
    dress_code = "dress_code"  # ドレスコード違反


class Severity(str, Enum):
    info = "info"
    warn = "warn"
    block = "block"  # ドレスコードの明確な NG


class HarmonyWarning(BaseModel):
    """§7-3: 判定根拠を必ず持たせる。根拠なしの警告は作らない。"""

    kind: WarningKind
    severity: Severity
    member_uids: list[str]
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)  # 色差値・スコア等
    suggestions: list[str] = Field(default_factory=list)


class HarmonyReport(BaseModel):
    warnings: list[HarmonyWarning] = Field(default_factory=list)
    formality_median: float | None = None
    evaluated_at: datetime = Field(default_factory=now)
    explanation: str | None = None  # Gemini の総評（任意）

    @property
    def is_clear(self) -> bool:
        return not any(w.severity != Severity.info for w in self.warnings)


# --------------------------------------------------------------------------- プレビュー


class PreviewRevision(BaseModel):
    revision: int
    image_ref: str
    composed_uids: list[str] = Field(default_factory=list)  # 顔を合成したメンバー
    silhouette_uids: list[str] = Field(default_factory=list)  # 未同意でシルエットの人
    created_at: datetime = Field(default_factory=now)
    reason: str = ""
    engine: str = "local"  # どの合成エンジンで作ったか
    lighting: LightingPreset = LightingPreset.none


class Preview(BaseModel):
    current: PreviewRevision | None = None
    history: list[PreviewRevision] = Field(default_factory=list)


# --------------------------------------------------------------------------- 手配


class ArrangeItem(BaseModel):
    member_uid: str
    garment_name: str
    rental_url: str | None = None
    price_yen: int = 0
    done: bool = False


class ArrangePlan(BaseModel):
    items: list[ArrangeItem] = Field(default_factory=list)
    reminders: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)


# --------------------------------------------------------------------------- 記念ムービー


class Movie(BaseModel):
    """確定した集合プレビューから作る短い記念ムービー（設計書 §12）。

    合意形成が終わったことを祝う体験のためのもので、判定や手配には影響しない。
    生成は重いので自動では作らず、明示的に依頼されたときだけ作る。
    """

    movie_ref: str
    content_type: str = "image/gif"
    seconds: float = 3.0
    engine: str = "local"  # どの生成エンジンで作ったか
    has_audio: bool = False
    source_revision: int = 0  # 元にした集合プレビューのリビジョン
    created_at: datetime = Field(default_factory=now)


# --------------------------------------------------------------------------- 通知


class NotificationKind(str, Enum):
    harmony = "harmony"  # かぶり・浮きの検知
    pending = "pending"  # 衣装未確定の催促
    ready = "ready"  # 全員確定のお知らせ


class Notification(BaseModel):
    """アプリ内通知。ルームに閉じて保持し、TTL 削除にそのまま乗る。

    エージェントは advance() のたびに検知をやり直すため、同じ内容が積み上がらないよう
    dedupe_key で未読の重複を弾く（実際の抑制は ArrangerAgent が行う）。
    """

    notification_id: str = Field(default_factory=lambda: new_id("ntf"))
    to_uid: str
    kind: NotificationKind
    text: str
    created_at: datetime = Field(default_factory=now)
    read_at: datetime | None = None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def dedupe_key(self) -> str:
        return f"{self.to_uid}:{self.kind.value}:{self.text}"


# --------------------------------------------------------------------------- ルーム


class RoomStatus(str, Enum):
    open = "open"  # 参加者募集・試着中
    all_confirmed = "all_confirmed"  # 全員が衣装確定
    arranged = "arranged"  # 手配リンク配布済み
    purged = "purged"  # TTL または手動で全消去済み


class Room(BaseModel):
    """rooms/{roomId} の集約ルート。"""

    room_id: str = Field(default_factory=lambda: new_id("room"))
    event: EventInfo
    status: RoomStatus = RoomStatus.open
    members: list[Member] = Field(default_factory=list)
    fittings: dict[str, Fitting] = Field(default_factory=dict)
    preview: Preview = Field(default_factory=Preview)
    harmony: HarmonyReport = Field(default_factory=HarmonyReport)
    arrange: ArrangePlan | None = None
    notifications: list[Notification] = Field(default_factory=list)
    movie: Movie | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    # ---- メンバー参照 ----

    def member(self, uid: str) -> Member | None:
        for m in self.members:
            if m.uid == uid:
                return m
        return None

    @property
    def active_members(self) -> list[Member]:
        return [m for m in self.members if m.state != MemberState.left]

    @property
    def composable_members(self) -> list[Member]:
        return [m for m in self.active_members if m.composable]

    # ---- 進行判定 ----

    def confirmed_fittings(self) -> dict[str, Fitting]:
        """確定衣装のあるメンバーのみ。調和判定・合成の入力になる。"""
        out: dict[str, Fitting] = {}
        for m in self.active_members:
            f = self.fittings.get(m.uid)
            if f and f.selected:
                out[m.uid] = f
        return out

    @property
    def all_confirmed(self) -> bool:
        members = self.active_members
        return bool(members) and len(self.confirmed_fittings()) == len(members)

    # ---- 通知 ----

    def notifications_for(self, uid: str, *, unread_only: bool = False) -> list["Notification"]:
        """宛先本人ぶんだけを返す。通知は他メンバーには見せない。"""
        return [
            n
            for n in self.notifications
            if n.to_uid == uid and (not unread_only or not n.is_read)
        ]

    def unread_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.notifications:
            if not n.is_read:
                counts[n.to_uid] = counts.get(n.to_uid, 0) + 1
        return counts

    def ttl_at(self, days: int) -> datetime:
        """イベント日 + N 日（§6 ttl）。"""
        return datetime.combine(
            self.event.event_date, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(days=days)

    def is_expired(self, days: int, at: datetime | None = None) -> bool:
        return (at or now()) >= self.ttl_at(days)


# --------------------------------------------------------------------------- 監査


class AuditAction(str, Enum):
    room_create = "room_create"
    member_join = "member_join"
    consent_grant = "consent_grant"
    consent_revoke = "consent_revoke"
    tryon = "tryon"
    garment_select = "garment_select"
    preview_compose = "preview_compose"
    movie_create = "movie_create"
    harmony_evaluate = "harmony_evaluate"
    arrange = "arrange"
    reminder = "reminder"
    export = "export"
    purge = "purge"
    external_call = "external_call"


class AuditLog(BaseModel):
    """audit/{logId} — 同意取得・撤回・削除実行の証跡（設計書 §7-4）。"""

    log_id: str = Field(default_factory=lambda: new_id("aud"))
    room_id: str | None = None
    actor: str = "system"  # uid または agent 名
    action: AuditAction
    target: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    policy: str = ""
    created_at: datetime = Field(default_factory=now)
