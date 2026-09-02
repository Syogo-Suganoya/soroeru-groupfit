from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["mock", "live"]


class Settings(BaseSettings):
    """環境変数だけで mock / live を切り替える。キーが揃うまでは全て mock。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"

    # 外部API接続の切替（設計書 §5）
    gemini_mode: Mode = "mock"
    youcam_mode: Mode = "mock"

    # 重い画像合成・動画生成の実行先（設計書 §12）。
    # local = Pillow で完結。gmi = GMI Cloud の画像編集/動画生成モデルを使う。
    compositor_engine: Literal["local", "gmi"] = "local"
    video_engine: Literal["local", "gmi"] = "local"

    # 通知チャネル。既定はアプリ内通知（外部送信なし）。
    notify_channel: Literal["in_app", "line"] = "in_app"

    # データ管理は Firestore が既定。memory はテスト用（プロセス内保持）。
    db_driver: Literal["firestore", "memory"] = "firestore"
    storage_driver: Literal["local", "gcs"] = "local"

    storage_local_root: str = "/data/storage"
    gcs_bucket: str = ""
    google_cloud_project: str = "soroeru-groupfit-local"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    youcam_api_key: str = ""
    youcam_secret_key: str = ""
    line_channel_access_token: str = ""

    # GMI Cloud（設計書 §12）。エンドポイントとモデル名は差し替えられるようにしておく。
    gmi_api_key: str = ""
    gmi_base_url: str = "https://api.gmicloud.ai/v1"
    gmi_image_model: str = "seedream"  # 集合プレビューの品質エンジン
    gmi_relight_model: str = "bria-fibo-relight"  # 会場ライティング再現
    gmi_video_model: str = "image-to-video"  # 記念ムービー

    public_base_url: str = "http://localhost:8080"

    # --- 調和判定のしきい値（設計書 §4「調和判定ロジック」） ---
    # CIEDE2000 色差がこれ未満のペアを「色かぶり」として警告する。
    color_clash_delta_e: float = 12.0
    # フォーマル度（1-5）がグループ中央値からこれ以上離れたら「浮き」。
    formality_gap_threshold: float = 2.0

    # --- ガバナンス（設計書 §7） ---
    # イベント日 + N 日で全画像・ルームを削除する。
    ttl_days_after_event: int = 7
    consent_policy: str = "consent-based-composition; room-scoped; ttl-purge"


@lru_cache
def get_settings() -> Settings:
    return Settings()
