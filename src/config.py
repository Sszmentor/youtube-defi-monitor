"""Configuration management for YouTube DeFi Monitor."""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ViralityThreshold(BaseModel):
    """Threshold settings for a channel size category."""
    max_subs: Optional[int] = None
    ratio: float


class ViralityThresholds(BaseModel):
    """Adaptive virality thresholds based on channel size."""
    small: ViralityThreshold
    medium: ViralityThreshold
    large: ViralityThreshold


class MonitoringConfig(BaseModel):
    """Monitoring settings."""
    virality_thresholds: ViralityThresholds
    check_interval: str = "0 8 * * *"
    max_video_age_days: int = 7


class ChannelConfig(BaseModel):
    """YouTube channel configuration."""
    id: str
    name: str


class LLMConfig(BaseModel):
    """LLM API configuration."""
    provider: str = "anthropic"
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"


class TelegramConfig(BaseModel):
    """Telegram bot configuration."""
    bot_token: str = ""
    chat_id: str = ""


class FactCheckSource(BaseModel):
    """Fact-checking source configuration."""
    name: str
    enabled: bool = True
    base_url: str


class FactCheckConfig(BaseModel):
    """Fact-checking configuration."""
    sources: list[FactCheckSource] = []
    web_search_enabled: bool = True


class StyleConfig(BaseModel):
    """Content style configuration."""
    author_name: str = ""
    tone: str = "разговорный, но экспертный"
    language: str = "ru"
    examples_file: str = "prompts/style_examples.md"


class DatabaseConfig(BaseModel):
    """Database configuration."""
    path: str = "data/monitor.db"


class AppConfig(BaseModel):
    """Main application configuration."""
    youtube_api_key: str = ""
    channels: list[ChannelConfig] = []
    monitoring: MonitoringConfig
    llm: LLMConfig
    telegram: TelegramConfig
    factcheck: FactCheckConfig
    style: StyleConfig
    database: DatabaseConfig


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load configuration from YAML file with environment variable substitution."""
    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    # Substitute environment variables
    def substitute_env(value):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.getenv(env_var, "")
        return value

    def process_dict(d):
        result = {}
        for key, value in d.items():
            if isinstance(value, dict):
                result[key] = process_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    process_dict(item) if isinstance(item, dict) else substitute_env(item)
                    for item in value
                ]
            else:
                result[key] = substitute_env(value)
        return result

    processed_config = process_dict(raw_config)

    # Build AppConfig
    return AppConfig(
        youtube_api_key=processed_config.get("youtube", {}).get("api_key", ""),
        channels=[
            ChannelConfig(**ch) for ch in processed_config.get("channels", [])
        ],
        monitoring=MonitoringConfig(**processed_config.get("monitoring", {})),
        llm=LLMConfig(**processed_config.get("llm", {})),
        telegram=TelegramConfig(**processed_config.get("telegram", {})),
        factcheck=FactCheckConfig(**processed_config.get("factcheck", {})),
        style=StyleConfig(**processed_config.get("style", {})),
        database=DatabaseConfig(**processed_config.get("database", {})),
    )


# Global config instance
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        from dotenv import load_dotenv
        load_dotenv()
        _config = load_config()
    return _config
