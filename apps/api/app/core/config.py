from __future__ import annotations

import os
from dataclasses import dataclass

#: 环境变量布尔的宽容解析：1/true/yes/on 视为真，其余为假。
#: 2026-10-09 取证时踩到：只认字面 "true" 会把 BIDOS_AI_ENABLED=1 判成"未启用"，
#: 表现是模型看起来配好了、实际一个调用都没发。单独抽成函数以便回归测试。
_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("BIDOS_ENV", "development")
    data_mode: str = os.getenv("BIDOS_DATA_MODE", "mock")
    #: 租户口径（team-lead 裁定 B1）：
    #:   **读**：为空 → 不过滤，保持单租户原型的现有行为；非空 → 按此 org 过滤。
    #:   **写**：为空时回落到 DEFAULT_ORGANIZATION_ID（"org_default"），
    #:          因为 002_schema.sql 里 7 张表的 organization_id 是 NOT NULL + FK。
    organization_id: str = os.getenv("BIDOS_ORGANIZATION_ID", "")
    source_config_path: str = os.getenv("BIDOS_SOURCE_CONFIG", "/app/config/sources.yaml")
    database_url: str = os.getenv("DATABASE_URL", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    review_confidence_threshold: float = float(os.getenv("BIDOS_REVIEW_CONFIDENCE", "0.78"))
    allow_external_ai: bool = os.getenv("BIDOS_ALLOW_EXTERNAL_AI", "false").lower() == "true"
    # --- 模型能力接入（2026-10-09）-------------------------------------------------
    # 设计：适配层 + 显式开关。默认 **不启用**，因此接手后现有行为与结果一字不变；
    # 需要模型整理时再配 BIDOS_AI_ENABLED=1 与端点三件套（BASE_URL / MODEL / API_KEY）。
    #: 总开关。关闭时行内不发起任何模型调用。
    ai_enabled: bool = env_flag("BIDOS_AI_ENABLED")
    #: 供应商标识（仅用于如实标注来源：openai_compatible / workbuddy_cloud / ollama / deepseek ...）。
    ai_provider: str = os.getenv("BIDOS_AI_PROVIDER", "openai_compatible")
    #: OpenAI 兼容端点根地址，例如 http://127.0.0.1:11434/v1 或 https://api.deepseek.com/v1
    ai_base_url: str = os.getenv("BIDOS_AI_BASE_URL", "")
    #: 模型 id，例如 qwen3:27b / deepseek-chat
    ai_model: str = os.getenv("BIDOS_AI_MODEL", "")
    #: 凭据。为空表示端点无需鉴权（本地 Ollama 常见）。
    ai_api_key: str = os.getenv("BIDOS_AI_API_KEY", "")
    #: 单次调用超时（秒）。
    ai_timeout: float = float(os.getenv("BIDOS_AI_TIMEOUT", "60"))
    #: 显式代理（如 http://127.0.0.1:7890）；为空则不走环境代理。
    ai_proxy: str = os.getenv("BIDOS_AI_PROXY", "")
    #: 单轮采集最多用模型整理几条候选，避免一次采集把额度和耗时打满。
    ai_max_per_run: int = int(os.getenv("BIDOS_AI_MAX_PER_RUN", "3"))
    #: 送去整理的公告正文上限（字符）。
    ai_body_chars: int = int(os.getenv("BIDOS_AI_BODY_CHARS", "6000"))
    #: postgres 模式启动时等 DB 就绪的最长秒数（开机顺序自愈：容器比 launchd 晚就绪
    #: 是常态，2026-09-30 事故）。超时仍未就绪则退出交给 launchd 重启重试；
    #: 0 = 关闭等待（恢复旧的立即降级 mock 行为）。
    startup_db_wait: int = int(os.getenv("BIDOS_STARTUP_DB_WAIT", "120"))


settings = Settings()
