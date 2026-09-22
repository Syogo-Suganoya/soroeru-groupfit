"""シーン別ドレスコードプリセット。

MVP は wedding のみ実装し、他シーンは枠だけ用意する。
プリセットを1箇所に閉じ込めることで、成人式・コスプレはここへの追加だけで足りる。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.color import is_blackish, is_whiteish
from app.domain.models import Garment, SceneType, Severity


class RuleHit(BaseModel):
    rule: str
    severity: Severity
    message: str
    evidence: dict = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)


class ScenePreset(BaseModel):
    scene: SceneType
    label: str
    formality_floor: int = 1  # これ未満はカジュアルすぎ
    ng_white: bool = False
    ng_all_black: bool = False
    ng_fur: bool = False
    notes: list[str] = Field(default_factory=list)

    def check(self, garment: Garment) -> list[RuleHit]:
        hits: list[RuleHit] = []

        if self.ng_white and is_whiteish(garment.primary_color_hex):
            hits.append(
                RuleHit(
                    rule="white",
                    severity=Severity.block,
                    message=f"「{garment.name}」は白系です。花嫁の色のため結婚式では避けます。",
                    evidence={"primary_color": garment.primary_color_hex, "rule": "白・オフホワイトNG"},
                    suggestions=["ネイビー・くすみブルー系", "ボルドー・テラコッタ系"],
                )
            )

        if self.ng_all_black and is_blackish(garment.primary_color_hex):
            hits.append(
                RuleHit(
                    rule="all_black",
                    severity=Severity.warn,
                    message=f"「{garment.name}」は全身黒に近く、弔事の装いに見えることがあります。",
                    evidence={"primary_color": garment.primary_color_hex, "rule": "全身黒は要注意"},
                    suggestions=["明るい色の羽織り・小物を足す", "濃紺やチャコールに寄せる"],
                )
            )

        if self.ng_fur and garment.has_fur:
            hits.append(
                RuleHit(
                    rule="fur",
                    severity=Severity.block,
                    message=f"「{garment.name}」はファー素材です。殺生を連想させるため慶事では避けます。",
                    evidence={"material": "fur"},
                    suggestions=["ストール・ボレロに替える"],
                )
            )

        if garment.formality < self.formality_floor:
            hits.append(
                RuleHit(
                    rule="too_casual",
                    severity=Severity.warn,
                    message=f"「{garment.name}」は{self.label}にはカジュアルすぎる可能性があります。",
                    evidence={
                        "formality": garment.formality,
                        "required_min": self.formality_floor,
                    },
                    suggestions=["ジャケットやフォーマル小物を足す"],
                )
            )

        return hits


PRESETS: dict[SceneType, ScenePreset] = {
    SceneType.wedding: ScenePreset(
        scene=SceneType.wedding,
        label="結婚式",
        formality_floor=3,
        ng_white=True,
        ng_all_black=True,
        ng_fur=True,
        notes=["白は花嫁の色", "全身黒は弔事を連想", "ファー・アニマル柄は殺生の連想"],
    ),
    # --- 以下は MVP 外。枠のみ用意 ---
    SceneType.coming_of_age: ScenePreset(
        scene=SceneType.coming_of_age,
        label="成人式",
        formality_floor=4,
        notes=["MVP 未対応。柄かぶり検知のみ共通ロジックで動作する"],
    ),
    SceneType.cosplay: ScenePreset(
        scene=SceneType.cosplay,
        label="コスプレ合わせ",
        formality_floor=1,
        notes=["MVP 未対応。キャラ被り検知は将来拡張"],
    ),
}


def preset_for(scene: SceneType) -> ScenePreset:
    return PRESETS[scene]
