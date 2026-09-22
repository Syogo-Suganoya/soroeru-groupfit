"""集合プレビュー合成のポート。

複数人を1枚に自然に並べる処理は、スケールと並びの整合が製品価値に直結する。
現状は Pillow による描画で完結させている。将来もっと質の高い画像編集モデルに
差し替えるときも、呼び出し側（合成エージェント）はエンジンを知らないままにする。

差し替える実装を書くときの約束: ベース画像はローカルで作り、それを編集させる。
「誰をシルエットにするか」の判断がローカル側に残り、
同意していない人の顔が生成されうる余地を作らない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.rendering import figure


class CompositorPort(ABC):
    name = "compositor"
    engine = "local"

    @abstractmethod
    async def compose_group( self, *, figures: list[dict]) -> bytes:
        """集合プレビューのPNGを返す。

        figures の各要素: {name, color_hex, pattern, silhouette(bool), label}
        silhouette=True のメンバーは顔を出さない。この約束はエンジンによらず守る。
        """


class LocalCompositor(CompositorPort):
    """Pillow で描画する。"""

    name = "local-compositor"
    engine = "local"

    async def compose_group( self, *, figures: list[dict]) -> bytes:
        return figure.render_group_preview(figures)
