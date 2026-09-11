"""YouCam live 実装のテスト。

実APIは叩かず、httpx.MockTransport で YouCam 側を再現する。
確かめたいのは3つ:
  1. ドキュメントどおりの順序で呼ぶこと（File API → 実体PUT → タスク → ポーリング）
  2. 失敗しても mock に落ちてルーム進行が止まらないこと
  3. どちらで作ったかが TryOnResult.engine に残ること
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.catalog import find
from app.domain.color import personal_color_season
from app.ports.storage import LocalStoragePort
from app.ports.tryon import MockTryOnPort, YouCamTryOnPort

GARMENT = find("g_navy_satin").model_copy(
    update={"reference_image_url": "https://example.com/navy.png"}
)
RESULT_PNG = b"\x89PNG\r\n\x1a\n-youcam-result"
UPLOAD_URL = "https://s3.example.com/upload?sig=abc"
RESULT_URL = "https://s3.example.com/result.png"


class FakeYouCam:
    """YouCam API の最小再現。受け取ったリクエストを順に記録する。"""

    def __init__(self, *, running_polls: int = 1, fail_task: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.uploaded: bytes | None = None
        self.payloads: list[dict] = []
        self.running_polls = running_polls
        self.fail_task = fail_task
        self._polled = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, str(request.url)))

        if request.method == "PUT" and str(request.url) == UPLOAD_URL:
            self.uploaded = request.content
            return httpx.Response(200)

        if request.method == "GET" and str(request.url) == RESULT_URL:
            return httpx.Response(200, content=RESULT_PNG)

        if path == "/s2s/v2.0/file":
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "files": [
                            {
                                "file_id": "file-123",
                                "requests": [
                                    {
                                        "method": "PUT",
                                        "url": UPLOAD_URL,
                                        "headers": {"Content-Type": "image/jpg"},
                                    }
                                ],
                            }
                        ]
                    },
                },
            )

        if request.method == "POST":
            self.payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"status": 200, "data": {"task_id": "task-1"}})

        # ポーリング。指定回数 running を返してから決着させる。
        self._polled += 1
        if self._polled <= self.running_polls:
            return httpx.Response(200, json={"status": 200, "data": {"task_status": "running"}})
        if self.fail_task:
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {"task_status": "error", "error": "error_pose"},
                },
            )
        if "skin-tone-analysis" in path:
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "task_status": "success",
                        "results": {"color": {"skin_color": "#b9947c"}},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": 200,
                "data": {"task_status": "success", "results": {"url": RESULT_URL}},
            },
        )


@pytest.fixture
def storage(tmp_path):
    return LocalStoragePort(str(tmp_path))


def build_port(storage, fake: FakeYouCam) -> YouCamTryOnPort:
    port = YouCamTryOnPort(storage, "test-key", poll_interval=0.0, timeout=5.0)
    transport = httpx.MockTransport(fake.handler)
    port._client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url=port.base_url,
        transport=transport,
        headers={"Authorization": f"Bearer {port.api_key}"},
    )
    return port


async def put_photo(storage) -> str:
    return await storage.put(
        room_id="room_1", key="members/u1/photo.jpg", data=b"selfie-bytes"
    )


# ---------------------------------------------------------------- 正常系


async def test_try_on_follows_the_documented_call_order(storage):
    fake = FakeYouCam()
    port = build_port(storage, fake)
    photo_ref = await put_photo(storage)

    result = await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=GARMENT,
        photo_ref=photo_ref,
    )

    steps = [(m, httpx.URL(u).path if u.startswith("https://yce") else u) for m, u in fake.calls]
    assert steps[0] == ("POST", "/s2s/v2.0/file")  # アップロード先の発行
    assert steps[1] == ("PUT", UPLOAD_URL)  # 実体を送るまで完了しない
    assert steps[2] == ("POST", "/s2s/v2.0/task/cloth-v4")
    assert steps[3][0] == "GET" and "/task/cloth-v4/task-1" in steps[3][1]

    assert fake.uploaded == b"selfie-bytes"  # 本人の写真がそのまま送られている
    assert fake.payloads[0] == {
        "src_file_id": "file-123",
        "ref_file_url": "https://example.com/navy.png",
        "garment_category": "auto",
    }

    assert result.engine == "youcam"
    assert await storage.get(result.image_ref) == RESULT_PNG


async def test_tone_analysis_maps_skin_color_to_a_season(storage):
    fake = FakeYouCam()
    port = build_port(storage, fake)

    tone = await port.analyze_tone(
        room_id="room_1", member_uid="u1", photo_ref=await put_photo(storage)
    )

    assert tone == personal_color_season("#b9947c")
    assert fake.payloads[0] == {"src_file_id": "file-123"}


async def test_polling_continues_until_the_task_finishes(storage):
    fake = FakeYouCam(running_polls=3)
    port = build_port(storage, fake)

    await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=GARMENT,
        photo_ref=await put_photo(storage),
    )

    polls = [u for m, u in fake.calls if m == "GET" and "/task/cloth-v4/" in u]
    assert len(polls) == 4  # running ×3 のあと success


# ---------------------------------------------------------------- 異常系（すべて mock に落ちる）


async def test_missing_photo_falls_back_to_mock(storage):
    port = build_port(storage, FakeYouCam())

    result = await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=GARMENT,
        photo_ref=None,
    )

    assert result.engine == "mock"
    assert (await storage.get(result.image_ref)).startswith(b"\x89PNG")


async def test_garment_without_reference_image_falls_back_to_mock(storage):
    port = build_port(storage, FakeYouCam())
    bare = find("g_navy_satin")  # reference_image_url が無い既定のカタログ
    assert bare.reference_image_url is None

    result = await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=bare,
        photo_ref=await put_photo(storage),
    )
    assert result.engine == "mock"


async def test_engine_error_falls_back_to_mock(storage):
    port = build_port(storage, FakeYouCam(fail_task=True))

    result = await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=GARMENT,
        photo_ref=await put_photo(storage),
    )
    assert result.engine == "mock"


async def test_unreachable_api_falls_back_to_mock(storage):
    port = YouCamTryOnPort(
        storage, "test-key", base_url="http://127.0.0.1:9/unreachable", timeout=1.0
    )

    result = await port.try_on(
        room_id="room_1",
        member_uid="u1",
        member_name="花子",
        garment=GARMENT,
        photo_ref=await put_photo(storage),
    )
    assert result.engine == "mock"

    tone = await port.analyze_tone(
        room_id="room_1", member_uid="u1", photo_ref=await put_photo(storage)
    )
    assert tone == await MockTryOnPort(storage).analyze_tone(
        room_id="room_1", member_uid="u1"
    )
