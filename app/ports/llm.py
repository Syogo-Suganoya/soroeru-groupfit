"""Gemini のポート（フォーマル度判定・調和提案・ドレスコード解釈）。

判定の数値そのものはドメイン側（harmony.py）で決めており、LLM は
「言語化」と「自由記述ドレスコードの解釈」だけを担当する。
LLM が落ちても警告は出続ける ＝ 判定根拠の再現性を守るための線引き。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from app.domain import harmony as harmony_rules
from app.domain.models import HarmonyReport, Room


class LlmPort(ABC):
    name = "llm"

    @abstractmethod
    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> str:
        """調和レポートの総評を日本語で返す。"""

    @abstractmethod
    async def interpret_dress_code(self, *, text: str) -> dict:
        """自由記述のドレスコードを構造化する。"""


class StubLlmPort(LlmPort):
    """キー不要。ルールベースの決定的な出力を返す。"""

    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> str:
        head = harmony_rules.summarize(report)
        if report.is_clear:
            return f"{head} 全体の色味・フォーマル度は揃っています。"
        tips = [s for w in report.warnings for s in w.suggestions][:2]
        return f"{head}。対応案: " + " / ".join(tips) if tips else head

    async def interpret_dress_code(self, *, text: str) -> dict:
        t = text or ""
        return {
            "source_text": t,
            "ng_white": "白" in t or "white" in t.lower(),
            "ng_all_black": "黒" in t,
            "ng_fur": "ファー" in t or "fur" in t.lower(),
            "interpreted_by": "stub",
        }


class GeminiLlmPort(LlmPort):
    """live 実装。GEMINI_MODE=live のときのみ google-genai を import する。"""

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._fallback = StubLlmPort()

    async def _generate(self, prompt: str) -> str:
        res = await self._client.aio.models.generate_content(
            model=self._model, contents=prompt
        )
        return (res.text or "").strip()

    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> str:
        payload = {
            "scene": room.event.scene.value,
            "dress_code": room.event.dress_code,
            "warnings": [w.model_dump(mode="json") for w in report.warnings],
        }
        prompt = (
            "あなたはグループの装いを整えるスタイリストです。"
            "以下の検出結果を、断定を避けた200字以内の日本語の総評にしてください。"
            "数値の根拠には触れつつ、美的な正解を押し付けないこと。\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            return await self._generate(prompt) or await self._fallback.summarize_harmony(
                room=room, report=report
            )
        except Exception:  # LLM 障害時も判定は落とさない
            return await self._fallback.summarize_harmony(room=room, report=report)

    async def interpret_dress_code(self, *, text: str) -> dict:
        prompt = (
            "次のドレスコード指定を JSON にしてください。"
            'キーは ng_white, ng_all_black, ng_fur（いずれも真偽値）のみ。\n' + (text or "")
        )
        try:
            raw = await self._generate(prompt)
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            return {"source_text": text, **data, "interpreted_by": "gemini"}
        except Exception:
            return await self._fallback.interpret_dress_code(text=text)
