"""API の疎通テスト。コンテナ（deps）は一時ディレクトリに差し替えて使う。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.deps import Container
from app.main import app, container as container_dep
from tests.conftest import build_settings


@pytest.fixture
def client(tmp_path):
    # API テストは memory ドライバで完結させる（Firestore 経路は
    # tests/test_firestore_repository.py がエミュレータ相手に検証する）。
    # 差し替えるのはルート側が Depends している関数そのもの。
    test_container = Container(
        build_settings(storage_local_root=str(tmp_path / "storage"), db_driver="memory")
    )
    app.dependency_overrides[container_dep] = lambda: test_container
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def create_room(client) -> dict:
    res = client.post(
        "/api/rooms",
        json={
            "title": "友人の結婚式",
            "scene": "wedding",
            "event_date": (date.today() + timedelta(days=30)).isoformat(),
            "dress_code": "白NG",
            "organizer_name": "幹事",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_health_reports_modes(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["modes"]["youcam"] == "mock"
    assert body["modes"]["notify"] == "in_app"


def test_notifications_are_per_member(client):
    room = create_room(client)
    room_id = room["room_id"]
    organizer = room["members"][0]["uid"]
    hanako = client.post(
        f"/api/rooms/{room_id}/members", json={"display_name": "花子"}
    ).json()["uid"]

    client.post(f"/api/rooms/{room_id}/members/{organizer}/select", json={"garment_id": "g_navy_satin"})
    client.post(f"/api/rooms/{room_id}/remind")

    # 未確定の花子にだけ催促が届く
    assert client.get(f"/api/rooms/{room_id}/members/{organizer}/notifications").json() == []
    got = client.get(f"/api/rooms/{room_id}/members/{hanako}/notifications").json()
    assert [n["kind"] for n in got] == ["pending"]

    # 未読件数がルーム表示にも出る
    view = client.get(f"/api/rooms/{room_id}").json()
    assert next(m for m in view["members"] if m["uid"] == hanako)["unread_notifications"] == 1

    # 既読にすると未読から消える
    res = client.post(f"/api/rooms/{room_id}/members/{hanako}/notifications/read", json={})
    assert res.json()["marked_read"] == 1
    assert (
        client.get(
            f"/api/rooms/{room_id}/members/{hanako}/notifications?unread_only=true"
        ).json()
        == []
    )


def test_top_page_is_the_guide_and_app_is_separate(client):
    top = client.get("/")
    assert top.status_code == 200
    assert "使い方" in top.text  # トップは機能・使い方の紹介

    web_app = client.get("/app")
    assert web_app.status_code == 200
    assert 'id="view-enter"' in web_app.text  # こちらがルーム操作の本体


def test_invite_url_points_at_the_app(client):
    """招待URLを開いた人がいきなりルームを操作できること。"""
    room = create_room(client)
    assert room["invite_url"].endswith(f"/app?room={room['room_id']}")


def test_catalog_is_served(client):
    items = client.get("/api/catalog").json()
    assert any(g["garment_id"] == "g_navy_satin" for g in items)


def test_room_lifecycle_through_api(client):
    room = create_room(client)
    room_id = room["room_id"]
    assert room["invite_url"].endswith(room_id)
    organizer = room["members"][0]["uid"]

    joined = client.post(f"/api/rooms/{room_id}/members", json={"display_name": "花子"}).json()
    hanako = joined["uid"]

    # 同意 → プレビューに合成される
    view = client.post(
        f"/api/rooms/{room_id}/members/{organizer}/consent", json={"granted": True}
    ).json()
    assert next(m for m in view["members"] if m["uid"] == organizer)["composed_in_preview"]
    assert not next(m for m in view["members"] if m["uid"] == hanako)["composed_in_preview"]

    # 色かぶりする組み合わせを確定させる
    client.post(f"/api/rooms/{room_id}/members/{organizer}/select", json={"garment_id": "g_navy_satin"})
    view = client.post(
        f"/api/rooms/{room_id}/members/{hanako}/select", json={"garment_id": "g_navy_lace"}
    ).json()

    assert view["all_confirmed"] is True
    assert view["status"] == "arranged"
    kinds = {w["kind"] for w in view["harmony"]["warnings"]}
    assert "color_clash" in kinds

    # プレビュー画像が取れる
    png = client.get(f"/api/rooms/{room_id}/preview.png")
    assert png.status_code == 200
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 代替案が提示される
    alts = client.get(f"/api/rooms/{room_id}/members/{hanako}/alternatives").json()
    assert alts and all(a["garment_id"] != "g_navy_satin" for a in alts)

    # 監査ログに同意と合成の証跡が残る
    logs = client.get(f"/api/rooms/{room_id}/audit").json()["logs"]
    actions = {l["action"] for l in logs}
    assert {"room_create", "consent_grant", "preview_compose", "garment_select"} <= actions

    # 完全削除しても監査ログは残る
    purged = client.delete(f"/api/rooms/{room_id}").json()
    assert purged["deleted_objects"] > 0
    assert client.get(f"/api/rooms/{room_id}").status_code == 404
    assert any(l["action"] == "purge" for l in client.get(f"/api/rooms/{room_id}/audit").json()["logs"])


def test_unknown_garment_is_rejected(client):
    room = create_room(client)
    uid = room["members"][0]["uid"]
    res = client.post(
        f"/api/rooms/{room['room_id']}/members/{uid}/select", json={"garment_id": "nope"}
    )
    assert res.status_code == 400


def test_unknown_room_returns_404(client):
    assert client.get("/api/rooms/room_missing").status_code == 404
