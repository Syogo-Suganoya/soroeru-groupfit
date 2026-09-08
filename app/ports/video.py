"""記念ムービー生成のポート。

確定した集合プレビューから短いムービーを作り、合意形成を祝う体験にする。
現状は Pillow で作る無音のアニメーションGIF。
image-to-video のような生成モデルに差し替えるときも、この口を実装すればよい。
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
    """集合プレビューにゆっくり寄るGIFを作る。音楽は付かない。"""

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
