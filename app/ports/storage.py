"""ルーム内一時画像のストレージポート（ローカル / Cloud Storage）。

「ルーム単位の完全消去」を満たすため、全オブジェクトを room_id 配下に
閉じ込め、purge(room_id) 一発で消せる形にしている。個別削除は同意撤回時に使う。
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class StoragePort(ABC):
    name = "storage"

    @abstractmethod
    async def put(
        self, *, room_id: str, key: str, data: bytes, content_type: str = "image/png"
    ) -> str:
        """保存して参照文字列（ref）を返す。"""

    @abstractmethod
    async def get(self, ref: str) -> bytes | None: ...

    @abstractmethod
    async def delete_prefix(self, *, room_id: str, prefix: str = "") -> int:
        """room_id 配下（任意で prefix 以下）を削除し、削除件数を返す。"""


class LocalStoragePort(StoragePort):
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        return self.root / ref

    async def put(
        self, *, room_id: str, key: str, data: bytes, content_type: str = "image/png"
    ) -> str:
        ref = f"{room_id}/{key}"
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ref

    async def get(self, ref: str) -> bytes | None:
        path = self._path(ref)
        if not path.is_file():
            return None
        return path.read_bytes()

    async def delete_prefix(self, *, room_id: str, prefix: str = "") -> int:
        base = self._path(f"{room_id}/{prefix}".rstrip("/"))
        if not base.exists():
            return 0
        if base.is_file():
            base.unlink()
            return 1
        count = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base)
        return count


class GcsStoragePort(StoragePort):
    """live 実装。STORAGE_DRIVER=gcs のときのみ import される。"""

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage  # 遅延 import（mock では依存を要求しない）

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self.bucket_name = bucket

    async def put(
        self, *, room_id: str, key: str, data: bytes, content_type: str = "image/png"
    ) -> str:
        ref = f"{room_id}/{key}"
        self._bucket.blob(ref).upload_from_string(data, content_type=content_type)
        return ref

    async def get(self, ref: str) -> bytes | None:
        blob = self._bucket.blob(ref)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    async def delete_prefix(self, *, room_id: str, prefix: str = "") -> int:
        blobs = list(self._client.list_blobs(self._bucket, prefix=f"{room_id}/{prefix}"))
        for blob in blobs:
            blob.delete()
        return len(blobs)
