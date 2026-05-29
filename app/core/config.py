# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra="ignore"
    )

    APP_NAME: str = "notion-2api"
    APP_VERSION: str = "4.0.0" # 最终稳定版
    DESCRIPTION: str = "一个将 Notion AI 转换为兼容 OpenAI 格式 API 的高性能代理。"

    API_MASTER_KEY: Optional[str] = None

    # --- Notion 凭证 ---
    NOTION_COOKIE: Optional[str] = None
    NOTION_SPACE_ID: Optional[str] = None
    NOTION_SPACE_NAME: Optional[str] = None
    NOTION_SPACE_VIEW_ID: Optional[str] = None
    NOTION_USER_ID: Optional[str] = None
    NOTION_USER_NAME: Optional[str] = None
    NOTION_USER_EMAIL: Optional[str] = None
    NOTION_BLOCK_ID: Optional[str] = None
    NOTION_CLIENT_VERSION: Optional[str] = "23.13.20260529.0633"
    NOTION_REFERER: Optional[str] = "https://www.notion.so/"
    NOTION_PROXY: Optional[str] = None

    API_REQUEST_TIMEOUT: int = 180
    NGINX_PORT: int = 4002
    APP_PORT: int = 4003

    # 【最终修正】更新所有已知的模型列表
    DEFAULT_MODEL: str = "gpt-5.2"

    KNOWN_MODELS: List[str] = [
        "auto",
        "gpt-5.2",
        "gpt-5.4",
        "sonnet-4.6",
        "opus-4.7",
        "gpt-5",
        "claude-sonnet-4.5",
        "claude-opus-4.1",
        "gemini-2.5-flash（未修复，不可用）",
        "gemini-2.5-pro（未修复，不可用）",
        "gpt-4.1"
    ]

    # 【最终修正】根据您提供的信息，填充所有模型的真实后台名称
    MODEL_MAP: dict = {
        "auto": "",
        "gpt-5.2": "oatmeal-cookie",
        "gpt-5.4": "oval-kumquat-medium",
        "sonnet-4.6": "almond-croissant-low",
        "opus-4.7": "apricot-sorbet-medium",
        "gpt-5": "oatmeal-cookie",
        "claude-sonnet-4.5": "oatmeal-cookie",
        "claude-opus-4.1": "apricot-sorbet-medium",
        "gemini-2.5-flash（未修复，不可用）": "vertex-gemini-2.5-flash",
        "gemini-2.5-pro（未修复，不可用）": "vertex-gemini-2.5-pro",
        "gpt-4.1": "oatmeal-cookie"
    }

settings = Settings()
