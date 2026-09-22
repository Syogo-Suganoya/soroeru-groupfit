"""Gemini のポート（総評の言語化・ドレスコードの読み取り）。

判定の数値そのものはドメイン側（harmony.py）で決めており、LLM が担うのは次の2つだけ:

- ドレスコードの読み取り: 自由記述を NG の真偽値に落とす。ルーム作成時に1回だけ呼び、
  結果は EventInfo.dress_rules に保存する。判定はこの保存値を使うので再現できる
- 総評: 判定結果を全員向けの短い文章にする

LLM が落ちても警告は出続ける ＝ 判定根拠の再現性を守るための線引き。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.domain import harmony as harmony_rules
from app.domain.dresscode import effective_preset
from app.domain.models import DressRules, HarmonyReport, Room


class Summary(BaseModel):
    text: str
    by: str  # gemini | stub


class LlmPort(ABC):
    name = "llm"
    engine = "stub"  # 正常時にこのポートが総評を作るエンジン

    @abstractmethod
    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> Summary:
        """調和レポートの総評を日本語で返す。"""

    @abstractmethod
    async def interpret_dress_code(self, *, text: str) -> DressRules:
        """自由記述のドレスコードを NG の真偽値に落とす。"""


class StubLlmPort(LlmPort):
    """キー不要。ルールベースの決定的な出力を返す。"""

    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> Summary:
        head = harmony_rules.summarize(report)
        if report.is_clear:
            text = f"{head} 全体の色味・フォーマル度は揃っています。"
        else:
            tips = [s for w in report.warnings for s in w.suggestions][:2]
            text = f"{head}。対応案: " + " / ".join(tips) if tips else head
        return Summary(text=text, by="stub")

    async def interpret_dress_code(self, *, text: str) -> DressRules:
        t = text or ""
        return DressRules(
            ng_white="白" in t or "white" in t.lower(),
            ng_all_black="黒" in t,
            ng_fur=any(w in t for w in ("ファー", "毛皮")) or "fur" in t.lower(),
            interpreted_by="stub",
        )


class GeminiLlmPort(LlmPort):
    """live 実装。GEMINI_MODE=live のときのみ google-genai を import する。"""

    engine = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai
        from google.genai import errors

        # 総評もドレスコードの読み取りも、利用者の操作（作成・衣装の決定）の応答を待たせて作る。
        # 混雑時に数十秒かかることがあるため、10秒で打ち切って代替の文に落とす
        self._client = genai.Client(api_key=api_key, http_options={"timeout": 10_000})
        self._server_error = errors.ServerError
        self._model = model
        self._fallback = StubLlmPort()

    async def _generate(self, prompt: str, *, as_json: bool = False) -> str:
        # 混雑時の 503 は数秒で解けることが多いので1回だけ待ってやり直す。
        # それ以上は粘らない（操作の応答が遅くなるだけで、代替の文に落ちても進行は止まらない）
        for attempt in range(2):
            try:
                res = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"} if as_json else None,
                )
                return (res.text or "").strip()
            except self._server_error:
                if attempt:
                    raise
                await asyncio.sleep(1.5)
        return ""

    async def summarize_harmony(self, *, room: Room, report: HarmonyReport) -> Summary:
        # 渡すのは表示名と確定衣装の属性だけ。試着画像や顔写真は送らない。
        members = []
        for uid, fitting in room.confirmed_fittings().items():
            g = fitting.selected.garment  # type: ignore[union-attr]
            m = room.member(uid)
            members.append(
                {
                    "name": m.display_name if m else uid,
                    "garment": g.name,
                    "color": g.color_name,
                    "pattern": g.pattern.value,
                    "formality": g.formality,
                }
            )
        payload = {
            "scene": effective_preset(room.event).label,
            "dress_code": room.event.dress_code,
            "undecided": len(room.active_members) - len(members),
            "members": members,
            "warnings": [
                {"message": w.message, "severity": w.severity.value, "evidence": w.evidence}
                for w in report.warnings
            ],
        }
        prompt = (
            "あなたはグループの装いを整えるスタイリストです。"
            "以下は、お呼ばれの服装をそろえているグループの現状と、ルールで検出した指摘です。"
            "メンバー全員が読む総評を、日本語で150字以内・2〜3文で書いてください。\n"
            "- 最初に、並んだときの全体の印象を一言で伝える\n"
            "- 指摘があれば、優先して直すものを1つに絞って伝える。数値の根拠に軽く触れてよい\n"
            "- 指摘に無い問題を新たに作らない。特定の人を責める書き方や、美的な正解の押し付けを避ける\n"
            "- 前置き・見出し・箇条書き・絵文字は使わない\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            text = await self._generate(prompt)
            if text:
                return Summary(text=text, by="gemini")
        except Exception:  # LLM 障害時も判定は落とさない
            pass
        return await self._fallback.summarize_harmony(room=room, report=report)

    async def interpret_dress_code(self, *, text: str) -> DressRules:
        prompt = (
            "次はイベントの幹事が書いたドレスコードの指定です。"
            "参加者の服装として避けるよう指定されているものを判定し、JSON で返してください。\n"
            "キーは ng_white（白・オフホワイト・生成りを避ける）, ng_all_black（全身黒を避ける）, "
            "ng_fur（ファー・毛皮を避ける）の3つだけで、値は true / false。"
            "はっきり書かれていないものは false にしてください。\n"
            f"指定: {text}"
        )
        try:
            raw = await self._generate(prompt, as_json=True)
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            # 文字列の "true" などは採らない。読み取りは NG を足す方向にしか効かないため、
            # 曖昧な値を真に倒すと、指定されていない NG で人を止めることになる
            return DressRules(
                ng_white=data.get("ng_white") is True,
                ng_all_black=data.get("ng_all_black") is True,
                ng_fur=data.get("ng_fur") is True,
                interpreted_by="gemini",
            )
        except Exception:
            return await self._fallback.interpret_dress_code(text=text)
