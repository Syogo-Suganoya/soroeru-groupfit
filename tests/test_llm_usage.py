"""Gemini（LlmPort）の使いどころのテスト。

- ドレスコードの読み取りは作成時の1回だけで、シーンの NG を足す方向にしか効かない
- 総評は入力（確定衣装と指摘）が変わったときだけ作り直す
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.orchestrator import Orchestrator
from app.domain import catalog
from app.domain.harmony import evaluate
from app.domain.models import (
    DressRules,
    EventInfo,
    Fitting,
    Member,
    MemberState,
    Room,
    SceneType,
    TryOnResult,
    WarningKind,
)
from app.ports.compositor import LocalCompositor
from app.ports.llm import GeminiLlmPort, StubLlmPort
from app.ports.repository import MemoryRoomRepository
from app.ports.storage import LocalStoragePort
from app.ports.tryon import MockTryOnPort

THRESHOLDS = {"color_clash_delta_e": 12.0, "formality_gap_threshold": 2.0}


class CountingLlm(StubLlmPort):
    """呼ばれた回数を数える。中身は stub と同じ。"""

    def __init__(self) -> None:
        self.summaries = 0
        self.reads = 0

    async def summarize_harmony(self, **kw):
        self.summaries += 1
        return await super().summarize_harmony(**kw)

    async def interpret_dress_code(self, **kw):
        self.reads += 1
        return await super().interpret_dress_code(**kw)


@pytest.fixture
def llm() -> CountingLlm:
    return CountingLlm()


@pytest.fixture
def o(settings, messaging, llm) -> Orchestrator:
    storage = LocalStoragePort(settings.storage_local_root)
    return Orchestrator(
        settings=settings,
        repo=MemoryRoomRepository(),
        storage=storage,
        tryon=MockTryOnPort(storage),
        llm=llm,
        messaging=messaging,
        compositor=LocalCompositor(),
    )


def room_with(event: EventInfo, garment_id: str) -> Room:
    room = Room(event=event)
    member = Member(display_name="A", state=MemberState.confirmed)
    room.members.append(member)
    garment = catalog.find(garment_id)
    room.fittings[member.uid] = Fitting(
        member_uid=member.uid,
        candidates=[TryOnResult(garment=garment, image_ref="ref.png")],
        selected_garment_id=garment_id,
    )
    return room


def in_30_days() -> date:
    return date.today() + timedelta(days=30)


# ------------------------------------------------------------------ ドレスコードの読み取り


async def test_dress_code_is_read_once_at_creation(o, llm, event):
    room = await o.create_room(event=event, organizer_name="幹事")
    assert llm.reads == 1
    assert room.event.dress_rules.ng_white is True
    assert room.event.dress_rules.interpreted_by == "stub"

    room, _ = await o.join(room_id=room.room_id, display_name="花子")
    assert llm.reads == 1  # 以後の判定は保存した読み取り結果を使う


async def test_empty_dress_code_is_not_read(o, llm):
    room = await o.create_room(
        event=EventInfo(event_date=in_30_days(), dress_code="  "), organizer_name="幹事"
    )
    assert llm.reads == 0
    assert room.event.dress_rules is None


def test_dress_code_adds_ng_the_scene_does_not_have():
    # コスプレ合わせには白NGの既定がない。幹事が書いたときだけ効く
    plain = EventInfo(scene=SceneType.cosplay, event_date=in_30_days())
    assert not [w for w in evaluate(room_with(plain, "g_ivory"), **THRESHOLDS).warnings
                if w.kind == WarningKind.dress_code]

    specified = plain.model_copy(update={"dress_rules": DressRules(ng_white=True)})
    hits = [w for w in evaluate(room_with(specified, "g_ivory"), **THRESHOLDS).warnings
            if w.kind == WarningKind.dress_code]
    assert len(hits) == 1
    assert hits[0].severity.value == "block"
    assert hits[0].evidence["source"] == "ドレスコードの指定"
    assert "指定されています" in hits[0].message


def test_dress_code_never_loosens_the_scene():
    # 読み取りが「白NGではない」と返しても、結婚式の白NGは外れない
    event = EventInfo(
        event_date=in_30_days(), dress_code="平服で", dress_rules=DressRules(ng_white=False)
    )
    hits = [w for w in evaluate(room_with(event, "g_ivory"), **THRESHOLDS).warnings
            if w.evidence.get("rule") == "white"]
    assert len(hits) == 1
    assert hits[0].evidence["source"] == "結婚式の基本"


async def test_gemini_reading_accepts_only_real_booleans():
    port = GeminiLlmPort.__new__(GeminiLlmPort)  # SDK を import せずに解析部分だけ試す
    port._fallback = StubLlmPort()

    async def answer(prompt, **kw):
        return '```json\n{"ng_white": "true", "ng_all_black": 1, "ng_fur": true}\n```'

    port._generate = answer
    rules = await port.interpret_dress_code(text="白っぽい服とファーは控えて")
    # 文字列や数値は真に倒さない（読み取りは NG を足す方向にしか効かないため）
    assert (rules.ng_white, rules.ng_all_black, rules.ng_fur) == (False, False, True)
    assert rules.interpreted_by == "gemini"


async def test_gemini_reading_falls_back_to_stub_on_broken_output():
    port = GeminiLlmPort.__new__(GeminiLlmPort)
    port._fallback = StubLlmPort()

    async def broken(prompt, **kw):
        return "すみません、判定できません"

    port._generate = broken
    rules = await port.interpret_dress_code(text="白NG")
    assert rules.ng_white is True
    assert rules.interpreted_by == "stub"


# ------------------------------------------------------------------ 総評


async def test_summary_waits_until_someone_decides(o, llm, event):
    room = await o.create_room(event=event, organizer_name="幹事")
    assert llm.summaries == 0
    assert room.harmony.explanation is None  # 誰も決めていないのに「揃っています」とは言わない


async def test_summary_is_reused_while_inputs_are_unchanged(o, llm, event):
    room = await o.create_room(event=event, organizer_name="幹事")
    organizer = room.members[0].uid
    room = await o.select_garment(room_id=room.room_id, uid=organizer, garment_id="g_navy_satin")
    assert llm.summaries == 1
    first = room.harmony.explanation
    assert first and room.harmony.explanation_by == "stub"

    # 同意や試着のやり直しは総評の入力を変えないので、作り直さない
    room = await o.set_consent(room_id=room.room_id, uid=organizer, granted=True)
    room = await o.run_try_on(room_id=room.room_id, uid=organizer)
    assert llm.summaries == 1
    assert room.harmony.explanation == first

    # 衣装を変えたら作り直す
    room = await o.select_garment(room_id=room.room_id, uid=organizer, garment_id="g_bordeaux")
    assert llm.summaries == 2


async def test_fallback_summary_is_retried_next_time(o, llm, event):
    # Gemini が落ちて stub の文になった回は、入力が同じでも次の機会に作り直す
    llm.engine = "gemini"
    room = await o.create_room(event=event, organizer_name="幹事")
    organizer = room.members[0].uid
    room = await o.select_garment(room_id=room.room_id, uid=organizer, garment_id="g_navy_satin")
    assert room.harmony.explanation_by == "stub"
    await o.set_consent(room_id=room.room_id, uid=organizer, granted=True)
    assert llm.summaries == 2
