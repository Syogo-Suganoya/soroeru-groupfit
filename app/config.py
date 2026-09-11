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
    # YouCam（Perfect Corp）。YOUCAM_MODE=live のときだけ使う。
    youcam_base_url: str = "https://yce-api-01.makeupar.com"
    # full_body / upper_body / lower_body / shoes / outer / auto
    youcam_garment_category: str = "auto"
    youcam_poll_interval_seconds: float = 2.0
    youcam_timeout_seconds: float = 120.0
    # 本人写真の上限（YouCam 側の制限が 10MB）
    photo_max_bytes: int = 10 * 1024 * 1024
    line_channel_access_token: str = ""

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
