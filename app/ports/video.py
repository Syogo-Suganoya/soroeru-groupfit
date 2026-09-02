"""記念ムービー生成のポート（設計書 §12 image-to-video ＋ 音楽生成）。

確定した集合プレビューから短いムービーを作り、合意形成を祝う体験にする。
ローカルは無音のGIF、GMI Cloud は image-to-video と音楽生成を使う想定。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.rendering import movie as movie_renderer


class VideoResult:
    def __init__(
        self,
        *,
        data: bytes,
        content_type: str,
        seconds: float,
        engine: str,
        has_audio: bool,
    ) -> None:
        self.data = data
        self.content_type = content_type
        self.seconds = seconds
        self.engine = engine
        self.has_audio = has_audio


class VideoPort(ABC):
    name = "video"
    engine = "local"

    @abstractmethod
    async def from_image(self, *, image: bytes, prompt: str) -> VideoResult:
        """静止画1枚から短いムービーを作る。"""


class LocalVideoPort(VideoPort):
    """既定。集合プレビューにゆっくり寄るGIFを作る。音楽は付かない。"""

    name = "local-video"
    engine = "local"

    async def from_image(self, *, image: bytes, prompt: str) -> VideoResult:
        data, seconds = movie_renderer.render_keepsake_gif(image)
        return VideoResult(
            data=data,
            content_type="image/gif",
            seconds=seconds,
            engine=self.engine,
            has_audio=False,
        )


class GmiVideoPort(VideoPort):
    """GMI Cloud の image-to-video で生成する。

    **実APIでの疎通は未検証。** 生成には時間がかかるため、非同期ジョブを返す実装だった場合は
    ポーリングが必要になる。ここでは同期応答を前提にし、失敗時はローカルGIFに落とす。
    音楽生成は別モデルのため、現時点では音声なしで返す。
    """

    name = "gmi-video"
    engine = "gmi"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        video_model: str,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.video_model = video_model
        self.timeout = timeout
        self._fallback = LocalVideoPort()

    async def from_image(self, *, image: bytes, prompt: str) -> VideoResult:
        import base64

        import httpx

        payload = {
            "model": self.video_model,
            "prompt": prompt,
            "image": base64.b64encode(image).decode(),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/videos/generations",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                res.raise_for_status()
                data = res.json()

            video = await self._extract_video(data)
            return VideoResult(
                data=video,
                content_type="video/mp4",
                seconds=float(data.get("seconds", 4.0)),
                engine=self.engine,
                has_audio=False,
            )
        except Exception:
            return await self._fallback.from_image(image=image, prompt=prompt)

    @staticmethod
    async def _extract_video(data: dict) -> bytes:
        import base64

        import httpx

        item: dict = {}
        if isinstance(data.get("data"), list) and data["data"]:
            item = data["data"][0]
        else:
            item = data

        b64 = item.get("b64_json") or item.get("video")
        if b64:
            return base64.b64decode(b64)

        url = item.get("url") or item.get("video_url")
        if url:
            async with httpx.AsyncClient(timeout=180) as client:
                res = await client.get(url)
                res.raise_for_status()
                return res.content

        raise ValueError("GMI Cloud の応答から動画を取り出せませんでした")
