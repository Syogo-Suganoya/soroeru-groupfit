"""ソロエル API Gateway（Cloud Run: api）。

エンドポイントは薄く保ち、進行の判断は Orchestrator に委ねる（設計書 §4）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.keepsake import KeepsakeAgent
from app.agents.orchestrator import MemberNotFound, Orchestrator, RoomNotFound
from app.config import Settings
from app.deps import Container, get_container
from app.domain import catalog
from app.domain.models import EventInfo, Garment, Notification
from app.schemas import (
    ConsentRequest,
    CreateRoomRequest,
    FittingView,
    JoinRequest,
    LightingRequest,
    ReadNotificationsRequest,
    RoomView,
    SelectRequest,
    TryOnRequest,
)

app = FastAPI(title="ソロエル", description="グループ集合試着エージェント", version="0.1.0")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def container() -> Container:
    return get_container()


def orchestrator(c: Container = Depends(container)) -> Orchestrator:
    return c.orchestrator


def settings(c: Container = Depends(container)) -> Settings:
    """コンテナが組み立てに使った設定をそのまま返す。

    グローバルの `get_settings()` を直接引かないこと。コンテナだけ差し替えたときに
    「ポートは mock なのに /health は live と言う」といった食い違いが起きる。
    """
    return c.settings


def _view(room, s: Settings) -> RoomView:
    return RoomView.of(
        room,
        base_url=s.public_base_url,
        ttl_days=s.ttl_days_after_event,
        # 作成可否はルーム状態だけで決まるので、コンテナに触らず判定する
        movie_availability=KeepsakeAgent.can_create(room),
    )


@app.exception_handler(RoomNotFound)
async def _room_not_found(_, exc: RoomNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"ルームが見つかりません: {exc}"})


@app.exception_handler(MemberNotFound)
async def _member_not_found(_, exc: MemberNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"メンバーが見つかりません: {exc}"})


# パスは /healthz にしないこと。Cloud Run では Google のフロントエンドが
# /healthz を横取りし、アプリに届く前に 404 を返す。
@app.get("/health")
async def health(s: Settings = Depends(settings)) -> dict:
    return {
        "status": "ok",
        "modes": {
            "gemini": s.gemini_mode,
            "notify": s.notify_channel,
            "db": s.db_driver,
            "storage": s.storage_driver,
        },
    }


@app.get("/api/catalog", response_model=list[Garment])
async def get_catalog() -> list[Garment]:
    return catalog.all_garments()


# ------------------------------------------------------------------ ルーム


@app.post("/api/rooms", response_model=RoomView, status_code=201)
async def create_room(
    body: CreateRoomRequest,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    room = await o.create_room(
        event=EventInfo(
            scene=body.scene,
            title=body.title,
            event_date=body.event_date,
            dress_code=body.dress_code,
            lighting=body.lighting,
        ),
        organizer_name=body.organizer_name,
    )
    return _view(room, s)


@app.get("/api/rooms/{room_id}", response_model=RoomView)
async def get_room(
    room_id: str,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    return _view(await o.get_room(room_id), s)


@app.post("/api/rooms/{room_id}/members", status_code=201)
async def join_room(
    room_id: str,
    body: JoinRequest,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> dict:
    room, member = await o.join(room_id=room_id, display_name=body.display_name)
    return {"uid": member.uid, "room": _view(room, s).model_dump(mode="json")}


@app.post("/api/rooms/{room_id}/members/{uid}/consent", response_model=RoomView)
async def set_consent(
    room_id: str,
    uid: str,
    body: ConsentRequest,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    room = await o.set_consent(room_id=room_id, uid=uid, granted=body.granted)
    return _view(room, s)


# ------------------------------------------------------------------ 試着


@app.post("/api/rooms/{room_id}/members/{uid}/tryon", response_model=RoomView)
async def try_on(
    room_id: str,
    uid: str,
    body: TryOnRequest | None = None,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    try:
        room = await o.run_try_on(
            room_id=room_id, uid=uid, garment_ids=(body.garment_ids if body else None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(room, s)


@app.post("/api/rooms/{room_id}/members/{uid}/select", response_model=RoomView)
async def select_garment(
    room_id: str,
    uid: str,
    body: SelectRequest,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    if catalog.find(body.garment_id) is None:
        raise HTTPException(status_code=400, detail=f"未知の衣装ID: {body.garment_id}")
    room = await o.select_garment(room_id=room_id, uid=uid, garment_id=body.garment_id)
    return _view(room, s)


@app.get("/api/rooms/{room_id}/members/{uid}/fitting", response_model=FittingView)
async def get_fitting(
    room_id: str,
    uid: str,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> FittingView:
    """本人の試着候補（画像URL付き）。ルーム全体のビューには含めない。"""
    room = await o.get_room(room_id)
    if room.member(uid) is None:
        raise HTTPException(status_code=404, detail=f"メンバーが見つかりません: {uid}")
    return FittingView.of(room, uid, base_url=s.public_base_url)


@app.get("/api/rooms/{room_id}/members/{uid}/alternatives", response_model=list[Garment])
async def alternatives(
    room_id: str, uid: str, o: Orchestrator = Depends(orchestrator)
) -> list[Garment]:
    room = await o.get_room(room_id)
    return o.alternatives(room, uid)


@app.get("/api/rooms/{room_id}/members/{uid}/export")
async def export_member(
    room_id: str, uid: str, o: Orchestrator = Depends(orchestrator)
) -> dict:
    """本人の試着画像のみを書き出す（集合プレビューは対象外・§7-2）。"""
    return {"scope": "self_only", "image_refs": await o.export_member(room_id=room_id, uid=uid)}


# ------------------------------------------------------------------ プレビュー


@app.get("/api/rooms/{room_id}/preview.png")
async def preview_png(
    room_id: str, c: Container = Depends(container), rev: int | None = None
) -> Response:
    room = await c.orchestrator.get_room(room_id)
    if room.preview.current is None:
        raise HTTPException(status_code=404, detail="プレビューがまだありません")
    data = await c.storage.get(room.preview.current.image_ref)
    if data is None:
        raise HTTPException(status_code=404, detail="プレビュー画像が見つかりません")
    return Response(content=data, media_type="image/png")


@app.post("/api/rooms/{room_id}/lighting", response_model=RoomView)
async def set_lighting(
    room_id: str,
    body: LightingRequest,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    """会場の光環境を設定し、集合プレビューを作り直す（設計書 §12）。"""
    room = await o.set_lighting(room_id=room_id, lighting=body.lighting)
    return _view(room, s)


# ------------------------------------------------------------------ 記念ムービー


@app.post("/api/rooms/{room_id}/movie", response_model=RoomView)
async def create_movie(
    room_id: str,
    o: Orchestrator = Depends(orchestrator),
    s: Settings = Depends(settings),
) -> RoomView:
    """集合プレビューから記念ムービーを作る。全員確定が前提。"""
    try:
        room = await o.create_movie(room_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(room, s)


@app.get("/api/rooms/{room_id}/movie")
async def get_movie(room_id: str, c: Container = Depends(container)) -> Response:
    room = await c.orchestrator.get_room(room_id)
    if room.movie is None:
        raise HTTPException(status_code=404, detail="記念ムービーはまだありません")
    data = await c.storage.get(room.movie.movie_ref)
    if data is None:
        raise HTTPException(status_code=404, detail="記念ムービーが見つかりません")
    return Response(content=data, media_type=room.movie.content_type)


@app.get("/api/images/{ref:path}")
async def image(ref: str, c: Container = Depends(container)) -> Response:
    data = await c.storage.get(ref)
    if data is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return Response(content=data, media_type="image/png")


# ------------------------------------------------------------------ 手配・通知


@app.post("/api/rooms/{room_id}/remind")
async def remind(room_id: str, o: Orchestrator = Depends(orchestrator)) -> dict:
    return {"reminded": await o.remind(room_id)}


# ------------------------------------------------------------------ 通知（アプリ内）


@app.get("/api/rooms/{room_id}/members/{uid}/notifications", response_model=list[Notification])
async def notifications(
    room_id: str,
    uid: str,
    unread_only: bool = False,
    o: Orchestrator = Depends(orchestrator),
) -> list[Notification]:
    """宛先本人ぶんの通知。他メンバーの通知は返さない。"""
    return await o.notifications(room_id=room_id, uid=uid, unread_only=unread_only)


@app.post("/api/rooms/{room_id}/members/{uid}/notifications/read")
async def read_notifications(
    room_id: str,
    uid: str,
    body: ReadNotificationsRequest | None = None,
    o: Orchestrator = Depends(orchestrator),
) -> dict:
    """既読化。ID を省略すると本人ぶんの未読をすべて既読にする。"""
    count = await o.mark_read(
        room_id=room_id,
        uid=uid,
        notification_ids=(body.notification_ids if body else None),
    )
    return {"marked_read": count}


# ------------------------------------------------------------------ ガバナンス


@app.get("/api/rooms/{room_id}/audit")
async def audit(room_id: str, c: Container = Depends(container)) -> dict:
    logs = await c.repo.audits(room_id)
    return {
        "policy": c.settings.consent_policy,
        "logs": [log.model_dump(mode="json") for log in logs],
    }


@app.delete("/api/rooms/{room_id}")
async def purge(room_id: str, o: Orchestrator = Depends(orchestrator)) -> dict:
    return await o.purge(room_id=room_id, actor="organizer", reason="manual")


@app.post("/api/maintenance/sweep")
async def sweep(o: Orchestrator = Depends(orchestrator)) -> dict:
    """TTL 到来ルームの一括削除（Cloud Scheduler → Cloud Run）。"""
    return {"purged": await o.sweep_expired()}


# ------------------------------------------------------------------ PWA


@app.get("/")
async def index() -> FileResponse:
    """機能と使い方の紹介ページ。招待URLを受け取った人が最初に見る想定。"""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/app")
async def web_app() -> FileResponse:
    """ルーム操作の本体。招待URLはこちらを指す。"""
    return FileResponse(WEB_DIR / "app.html")


if WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
