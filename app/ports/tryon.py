"""バーチャル試着のポート（YouCam AI Clothes Try-On / AI Facial Color Tones Analyzer）。

mock 実装は Pillow で「その人の色」の人物カードを描き、実APIと同じ TryOnResult を返す。
live 実装に差し替えても呼び出し側（試着エージェント）は変更不要。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from app.domain.color import personal_color_season
from app.domain.models import Garment, TryOnResult
from app.ports.storage import StoragePort
from app.rendering import figure

logger = logging.getLogger(__name__)

SEASONS = ["spring", "summer", "autumn", "winter"]


class TryOnPort(ABC):
    name = "tryon"
    engine = "mock"

    @abstractmethod
    async def try_on(
        self,
        *,
        room_id: str,
        member_uid: str,
        member_name: str,
        garment: Garment,
        photo_ref: str | None = None,
    ) -> TryOnResult:
        """1着分の試着画像を生成する。

        photo_ref は本人が上げた写真のストレージ参照。live 実装はこれが要る。
        """

    @abstractmethod
    async def analyze_tone(
        self, *, room_id: str, member_uid: str, photo_ref: str | None = None
    ) -> str:
        """パーソナルカラー（spring / summer / autumn / winter）を返す。"""


class MockTryOnPort(TryOnPort):
    """外部キー不要のローカル生成。デモとテストはこれで完結する。"""

    name = "mock-tryon"
    engine = "mock"

    def __init__(self, storage: StoragePort) -> None:
        self.storage = storage

    def _tone_of(self, member_uid: str) -> str:
        return SEASONS[sum(member_uid.encode()) % len(SEASONS)]

    async def analyze_tone(
        self, *, room_id: str, member_uid: str, photo_ref: str | None = None
    ) -> str:
        return self._tone_of(member_uid)

    async def try_on(
        self,
        *,
        room_id: str,
        member_uid: str,
        member_name: str,
        garment: Garment,
        photo_ref: str | None = None,
    ) -> TryOnResult:
        png = figure.render_try_on_card(
            member_name=member_name,
            garment_name=garment.name,
            color_hex=garment.primary_color_hex,
            pattern=garment.pattern.value,
        )
        ref = await self.storage.put(
            room_id=room_id,
            key=f"fittings/{member_uid}/{garment.garment_id}.png",
            data=png,
            content_type="image/png",
        )
        tone = self._tone_of(member_uid)
        score = figure.tone_match_score(tone, garment.primary_color_hex)
        return TryOnResult(
            garment=garment,
            image_ref=ref,
            tone_match=score,
            note=f"パーソナルカラー {tone} との適合度 {score:.0%}",
            engine=self.engine,
        )


class YouCamError(RuntimeError):
    """YouCam API が期待どおりに応答しなかった。"""


class YouCamTryOnPort(TryOnPort):
    """live 実装。YouCam API（Perfect Corp）を叩く。

    どのAPIも「File API でアップロード先URLを貰う → 実体をPUT → タスク投入 →
    task_id をポーリング → 成功したら結果URLを取得」という同じ流れになっている。
    ポーリングは必須で、保持期間内に問い合わせないとタスクがタイムアウトする。

    **外部が落ちても進行は止めない。** 失敗時と前提が欠けているときは mock 実装に落とし、
    どちらで作ったかを TryOnResult.engine に残す（設計書 §5）。

    docs: https://docs.perfectcorp.com/reference/ai_clothes
    """

    name = "youcam-tryon"
    engine = "youcam"

    FILE_PATH = "/s2s/v2.0/file"
    CLOTH_TASK = "/s2s/v2.0/task/cloth-v4"
    TONE_TASK = "/s2s/v2.0/task/skin-tone-analysis"

    def __init__(
        self,
        storage: StoragePort,
        api_key: str,
        secret_key: str = "",
        *,
        base_url: str = "https://yce-api-01.makeupar.com",
        garment_category: str = "auto",
        poll_interval: float = 2.0,
        timeout: float = 120.0,
    ) -> None:
        self.storage = storage
        self.api_key = api_key
        # API コンソールで発行する現行のキーは Bearer 認証のみを使う。
        # 旧 S2S 方式の secret key は受け取るが送らない。
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.garment_category = garment_category
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._fallback = MockTryOnPort(storage)

    # ------------------------------------------------------------------ 公開API

    async def try_on(
        self,
        *,
        room_id: str,
        member_uid: str,
        member_name: str,
        garment: Garment,
        photo_ref: str | None = None,
    ) -> TryOnResult:
        try:
            png = await self._try_on_live(
                room_id=room_id, member_uid=member_uid, garment=garment, photo_ref=photo_ref
            )
        except Exception as exc:  # noqa: BLE001 — 理由を問わず進行は止めない
            logger.warning(
                "YouCam 試着に失敗したため mock にフォールバックします: %s", exc
            )
            return await self._fallback.try_on(
                room_id=room_id,
                member_uid=member_uid,
                member_name=member_name,
                garment=garment,
                photo_ref=photo_ref,
            )

        ref = await self.storage.put(
            room_id=room_id,
            key=f"fittings/{member_uid}/{garment.garment_id}.png",
            data=png,
            content_type="image/png",
        )
        tone = await self.analyze_tone(
            room_id=room_id, member_uid=member_uid, photo_ref=photo_ref
        )
        score = figure.tone_match_score(tone, garment.primary_color_hex)
        return TryOnResult(
            garment=garment,
            image_ref=ref,
            tone_match=score,
            note=f"パーソナルカラー {tone} との適合度 {score:.0%}",
            engine=self.engine,
        )

    async def analyze_tone(
        self, *, room_id: str, member_uid: str, photo_ref: str | None = None
    ) -> str:
        try:
            photo = await self._require_photo(photo_ref)
            async with self._client() as client:
                file_id = await self._upload(client, photo, file_name=f"{member_uid}.jpg")
                task_id = await self._run_task(
                    client, self.TONE_TASK, {"src_file_id": file_id}
                )
                data = await self._poll(client, self.TONE_TASK, task_id)

            skin_hex = (
                data.get("results", {}).get("color", {}).get("skin_color")
            )
            if not skin_hex:
                raise YouCamError("肌色（skin_color）が応答に含まれていません")
            return personal_color_season(skin_hex)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "YouCam パーソナルカラー解析に失敗したため mock にフォールバックします: %s",
                exc,
            )
            return await self._fallback.analyze_tone(
                room_id=room_id, member_uid=member_uid, photo_ref=photo_ref
            )

    # ------------------------------------------------------------------ 内部

    def _client(self):
        import httpx

        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def _require_photo(self, photo_ref: str | None) -> bytes:
        if not photo_ref:
            raise YouCamError("本人の写真が未登録です")
        photo = await self.storage.get(photo_ref)
        if not photo:
            raise YouCamError(f"写真の実体が見つかりません: {photo_ref}")
        return photo

    async def _try_on_live(
        self, *, room_id: str, member_uid: str, garment: Garment, photo_ref: str | None
    ) -> bytes:
        if not garment.reference_image_url:
            raise YouCamError(
                f"衣装 {garment.garment_id} に参考画像（reference_image_url）がありません"
            )
        photo = await self._require_photo(photo_ref)

        async with self._client() as client:
            src_file_id = await self._upload(client, photo, file_name=f"{member_uid}.jpg")
            task_id = await self._run_task(
                client,
                self.CLOTH_TASK,
                {
                    "src_file_id": src_file_id,
                    "ref_file_url": garment.reference_image_url,
                    "garment_category": self.garment_category,
                },
            )
            data = await self._poll(client, self.CLOTH_TASK, task_id)

            url = data.get("results", {}).get("url")
            if not url:
                raise YouCamError("結果画像のURLが応答に含まれていません")
            res = await client.get(url, headers={})
            res.raise_for_status()
            return res.content

    async def _upload(
        self,
        client,
        data: bytes,
        *,
        file_name: str,
        content_type: str = "image/jpg",
    ) -> str:
        """File API で発行されたURLへ実体をPUTし、file_id を返す。

        File API を呼ぶだけではアップロードは完了しない。返ってきたURLへ
        実体を送るまでタスクは 404 / 500 になる（ドキュメントの明示的な注意）。
        """
        res = await client.post(
            self.FILE_PATH,
            json={
                "files": [
                    {
                        "content_type": content_type,
                        "file_name": file_name,
                        "file_size": len(data),
                    }
                ]
            },
        )
        res.raise_for_status()
        files = res.json().get("data", {}).get("files", [])
        if not files:
            raise YouCamError("File API がファイル情報を返しませんでした")

        entry = files[0]
        file_id = entry.get("file_id")
        requests = entry.get("requests") or []
        if not file_id or not requests:
            raise YouCamError("File API の応答に file_id / アップロード先がありません")

        upload = requests[0]
        put = await client.request(
            upload.get("method", "PUT"),
            upload["url"],
            content=data,
            headers=upload.get("headers", {"Content-Type": content_type}),
        )
        put.raise_for_status()
        return file_id

    async def _run_task(self, client, path: str, payload: dict) -> str:
        res = await client.post(path, json=payload)
        res.raise_for_status()
        task_id = res.json().get("data", {}).get("task_id")
        if not task_id:
            raise YouCamError(f"{path} が task_id を返しませんでした")
        return task_id

    async def _poll(self, client, path: str, task_id: str) -> dict:
        """success / error になるまで問い合わせる。

        保持期間内にポーリングしないとタスクは成功していてもタイムアウト扱いになるため、
        投入したら必ずここまで呼び切る。
        """
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            res = await client.get(f"{path}/{task_id}")
            res.raise_for_status()
            data = res.json().get("data", {})
            status = data.get("task_status")

            if status == "success":
                return data
            if status == "error":
                raise YouCamError(
                    f"タスクが失敗しました: {data.get('error')} {data.get('error_message', '')}".strip()
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise YouCamError(f"タスクが {self.timeout} 秒以内に完了しませんでした")

            await asyncio.sleep(self.poll_interval)
