"""集合プレビュー合成のポート（設計書 §12 GMI Cloud 活用）。

複数人を1枚に自然に並べる処理は、ライティングとスケールの整合が製品価値に直結する。
ローカル実装（Pillow）で体験を成立させたうえで、品質を上げたいときに
GMI Cloud の画像編集モデル（Seedream / flux-kontext-pro）へ差し替える。

呼び出し側（合成エージェント）は、どちらのエンジンかを知らない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import LightingPreset
from app.rendering import figure


class CompositorPort(ABC):
    name = "compositor"
    engine = "local"

    @abstractmethod
    async def compose_group(
        self, *, figures: list[dict], lighting: LightingPreset
    ) -> bytes:
        """集合プレビューのPNGを返す。

        figures の各要素: {name, color_hex, pattern, silhouette(bool), label}
        silhouette=True のメンバーは顔を出さない。この約束はエンジンによらず守る。
        """


class LocalCompositor(CompositorPort):
    """既定。Pillow で描画し、ライティングは色調で近似する。"""

    name = "local-compositor"
    engine = "local"

    async def compose_group(
        self, *, figures: list[dict], lighting: LightingPreset
    ) -> bytes:
        png = figure.render_group_preview(figures)
        return figure.apply_lighting(png, lighting)


class GmiCompositor(CompositorPort):
    """GMI Cloud の画像編集モデルで合成する。

    ベース画像はローカルで作り、それを編集モデルに渡して質感を上げる方式にしている。
    こうすると「誰をシルエットにするか」の判断がローカル側に残り、
    同意していない人の顔が生成されうる余地を作らない（設計書 §7-1）。

    **実APIでの疎通は未検証。** エンドポイントとモデル名は環境変数で差し替えられるようにし、
    レスポンスは base64 / URL の両形式に対応させてある。失敗時はローカル合成にフォールバックする。
    """

    name = "gmi-compositor"
    engine = "gmi"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        image_model: str,
        relight_model: str,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.image_model = image_model
        self.relight_model = relight_model
        self.timeout = timeout
        self._fallback = LocalCompositor()

    async def compose_group(
        self, *, figures: list[dict], lighting: LightingPreset
    ) -> bytes:
        base_png = figure.render_group_preview(figures)
        prompt = self._build_prompt(figures, lighting)
        try:
            edited = await self._edit_image(
                model=self.image_model, image=base_png, prompt=prompt
            )
            if lighting is not LightingPreset.none and self.relight_model:
                edited = await self._edit_image(
                    model=self.relight_model, image=edited, prompt=lighting.prompt
                )
            return edited
        except Exception:
            # 合成が落ちてもルーム進行は止めない。品質は落ちるが体験は続く。
            return await self._fallback.compose_group(figures=figures, lighting=lighting)

    @staticmethod
    def _build_prompt(figures: list[dict], lighting: LightingPreset) -> str:
        people = len(figures)
        hidden = sum(1 for f in figures if f.get("silhouette"))
        parts = [
            f"A group photo of {people} people standing side by side, full body, same scale, "
            "consistent lighting and shadows, photorealistic, natural pose",
        ]
        if hidden:
            parts.append(
                f"keep {hidden} of them as plain grey silhouettes without faces"
            )
        if lighting.prompt:
            parts.append(lighting.prompt)
        return ". ".join(parts)

    async def _edit_image(self, *, model: str, image: bytes, prompt: str) -> bytes:
        import base64

        import httpx

        payload = {
            "model": model,
            "prompt": prompt,
            "image": base64.b64encode(image).decode(),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.base_url}/images/edits",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            res.raise_for_status()
            data = res.json()

        return await self._extract_image(data)

    @staticmethod
    async def _extract_image(data: dict) -> bytes:
        """base64 でも URL でも受け取れるようにしておく（応答形式が未確定のため）。"""
        import base64

        import httpx

        item: dict = {}
        if isinstance(data.get("data"), list) and data["data"]:
            item = data["data"][0]
        elif isinstance(data.get("output"), list) and data["output"]:
            first = data["output"][0]
            item = first if isinstance(first, dict) else {"url": first}
        else:
            item = data

        b64 = item.get("b64_json") or item.get("image") or item.get("b64")
        if b64:
            return base64.b64decode(b64)

        url = item.get("url") or item.get("image_url")
        if url:
            async with httpx.AsyncClient(timeout=60) as client:
                res = await client.get(url)
                res.raise_for_status()
                return res.content

        raise ValueError("GMI Cloud の応答から画像を取り出せませんでした")
