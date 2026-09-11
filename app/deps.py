"""依存の組み立て。環境変数（mock / live）でポート実装を差し替える唯一の場所。"""

from __future__ import annotations

from functools import lru_cache

from app.agents.orchestrator import Orchestrator
from app.config import Settings, get_settings
from app.ports.compositor import CompositorPort, LocalCompositor
from app.ports.llm import GeminiLlmPort, LlmPort, StubLlmPort
from app.ports.messaging import InAppMessagingPort, LineMessagingPort, MessagingPort
from app.ports.repository import (
    FirestoreRoomRepository,
    MemoryRoomRepository,
    RoomRepository,
)
from app.ports.storage import GcsStoragePort, LocalStoragePort, StoragePort
from app.ports.tryon import MockTryOnPort, TryOnPort, YouCamTryOnPort
from app.ports.video import LocalVideoPort, VideoPort


def build_storage(s: Settings) -> StoragePort:
    if s.storage_driver == "gcs":
        return GcsStoragePort(s.gcs_bucket)
    return LocalStoragePort(s.storage_local_root)


def build_repository(s: Settings) -> RoomRepository:
    if s.db_driver == "firestore":
        return FirestoreRoomRepository(s.google_cloud_project)
    return MemoryRoomRepository()


def build_tryon(s: Settings, storage: StoragePort) -> TryOnPort:
    if s.youcam_mode == "live":
        return YouCamTryOnPort(
            storage,
            s.youcam_api_key,
            s.youcam_secret_key,
            base_url=s.youcam_base_url,
            garment_category=s.youcam_garment_category,
            poll_interval=s.youcam_poll_interval_seconds,
            timeout=s.youcam_timeout_seconds,
        )
    return MockTryOnPort(storage)


def build_llm(s: Settings) -> LlmPort:
    if s.gemini_mode == "live":
        return GeminiLlmPort(s.gemini_api_key, s.gemini_model)
    return StubLlmPort()


def build_messaging(s: Settings) -> MessagingPort:
    if s.notify_channel == "line":
        return LineMessagingPort(s.line_channel_access_token)
    return InAppMessagingPort()


def build_compositor(s: Settings) -> CompositorPort:
    return LocalCompositor()


def build_video(s: Settings) -> VideoPort:
    return LocalVideoPort()


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = build_storage(settings)
        self.repo = build_repository(settings)
        self.tryon = build_tryon(settings, self.storage)
        self.llm = build_llm(settings)
        self.messaging = build_messaging(settings)
        self.compositor = build_compositor(settings)
        self.video = build_video(settings)
        self.orchestrator = Orchestrator(
            settings=settings,
            repo=self.repo,
            storage=self.storage,
            tryon=self.tryon,
            llm=self.llm,
            messaging=self.messaging,
            compositor=self.compositor,
            video=self.video,
        )


@lru_cache
def get_container() -> Container:
    return Container(get_settings())
