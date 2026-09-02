from __future__ import annotations

import tempfile
from datetime import date, timedelta

import pytest

from app.agents.orchestrator import Orchestrator
from app.config import Settings
from app.domain.models import EventInfo
from app.ports.compositor import LocalCompositor
from app.ports.llm import StubLlmPort
from app.ports.messaging import InAppMessagingPort
from app.ports.repository import MemoryRoomRepository
from app.ports.storage import LocalStoragePort
from app.ports.tryon import MockTryOnPort
from app.ports.video import LocalVideoPort


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(storage_local_root=str(tmp_path / "storage"), db_driver="memory")


@pytest.fixture
def messaging() -> InAppMessagingPort:
    return InAppMessagingPort()


@pytest.fixture
def orchestrator(settings, messaging) -> Orchestrator:
    storage = LocalStoragePort(settings.storage_local_root)
    return Orchestrator(
        settings=settings,
        repo=MemoryRoomRepository(),
        storage=storage,
        tryon=MockTryOnPort(storage),
        llm=StubLlmPort(),
        messaging=messaging,
        compositor=LocalCompositor(),
        video=LocalVideoPort(),
    )


@pytest.fixture
def event() -> EventInfo:
    return EventInfo(
        title="友人の結婚式",
        event_date=date.today() + timedelta(days=30),
        dress_code="白NG",
    )
