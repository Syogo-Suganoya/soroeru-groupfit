"""本人写真の登録・削除（YouCam live 試着の入力）。

写真は最も戻せない情報なので、ルーム配下に閉じることと、
同意撤回・ルーム削除で実体まで消えることを確かめる。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.deps import Container
from app.main import app, container as container_dep
from tests.conftest import build_settings

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64  # 実体は見ないので最小のダミー


@pytest.fixture
def client(tmp_path):
    test_container = Container(
        build_settings(storage_local_root=str(tmp_path / "storage"), db_driver="memory")
    )
    app.dependency_overrides[container_dep] = lambda: test_container
    with TestClient(app) as c:
        c.container = test_container  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def create_room(client) -> dict:
    return client.post(
        "/api/rooms",
        json={
            "title": "友人の結婚式",
            "scene": "wedding",
            "event_date": (date.today() + timedelta(days=30)).isoformat(),
            "organizer_name": "幹事",
        },
    ).json()


def upload(client, room_id: str, uid: str, data: bytes = JPEG, ctype: str = "image/jpeg"):
    return client.post(
        f"/api/rooms/{room_id}/members/{uid}/photo",
        files={"file": ("me.jpg", data, ctype)},
    )


def test_photo_upload_is_visible_as_a_flag_only(client):
    room = create_room(client)
    uid = room["members"][0]["uid"]
    assert room["members"][0]["has_photo"] is False

    view = upload(client, room["room_id"], uid).json()
    me = next(m for m in view["members"] if m["uid"] == uid)
    assert me["has_photo"] is True
    # 写真の参照先はルームのビューに出さない
    assert "photo_ref" not in me and "photo_url" not in me


def test_photo_lives_under_the_room_so_purge_removes_it(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]
    upload(client, room_id, uid)

    storage = client.container.storage  # type: ignore[attr-defined]
    saved = list((storage.root / room_id / "members" / uid).glob("photo.*"))
    assert len(saved) == 1 and saved[0].read_bytes() == JPEG

    client.delete(f"/api/rooms/{room_id}")
    assert not (storage.root / room_id).exists()


def test_revoking_consent_deletes_the_photo(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]
    client.post(f"/api/rooms/{room_id}/members/{uid}/consent", json={"granted": True})
    upload(client, room_id, uid)

    view = client.post(
        f"/api/rooms/{room_id}/members/{uid}/consent", json={"granted": False}
    ).json()

    assert next(m for m in view["members"] if m["uid"] == uid)["has_photo"] is False
    storage = client.container.storage  # type: ignore[attr-defined]
    assert not (storage.root / room_id / "members" / uid).exists()


def test_photo_can_be_deleted_by_the_owner(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]
    upload(client, room_id, uid)

    view = client.delete(f"/api/rooms/{room_id}/members/{uid}/photo").json()
    assert next(m for m in view["members"] if m["uid"] == uid)["has_photo"] is False

    logs = client.get(f"/api/rooms/{room_id}/audit").json()["logs"]
    assert {"photo_upload", "photo_delete"} <= {log["action"] for log in logs}


def test_retaking_replaces_the_previous_file(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]
    upload(client, room_id, uid)
    upload(client, room_id, uid, data=b"\x89PNG\r\n\x1a\n" + b"1" * 32, ctype="image/png")

    storage = client.container.storage  # type: ignore[attr-defined]
    saved = sorted(p.name for p in (storage.root / room_id / "members" / uid).iterdir())
    assert saved == ["photo.png"]  # jpg が残らない


def test_non_image_uploads_are_rejected(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]

    res = client.post(
        f"/api/rooms/{room_id}/members/{uid}/photo",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 400


def test_oversized_photo_is_rejected(client):
    room = create_room(client)
    room_id, uid = room["room_id"], room["members"][0]["uid"]

    res = upload(client, room_id, uid, data=b"\xff\xd8\xff\xe0" + b"0" * (10 * 1024 * 1024))
    assert res.status_code == 413
