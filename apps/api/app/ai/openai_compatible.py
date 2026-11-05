"""OpenAI 兼容 HTTP 模型 Provider（2026-10-09 新增）。

为什么用「OpenAI 兼容」这一层而不是直连某家厂商：
本地 Ollama / vLLM / LM Studio、以及国内主流厂商的开放平台，都提供
``POST {base_url}/chat/completions`` 形态的接口。项目后台守护进程（采集 / 解析 /
调度）只需要一个能被**进程内直接调用**的端点，因此适配层落在这一层最通用。

两个实现细节来自实测约束，不是偏好：

1. **默认走流式（``stream: true``）。** WorkBuddy 云服务的 LLM API 只支持流式，
   非流式请求会在发网络包之前就被拒绝（``request_stream_required``）。把流式设为
   默认，同一份代码既能对话兼容端点，也能对接只支持 SSE 的网关。
2. **同时兼容非流式响应。** 部分自建端点会忽略 ``stream`` 直接返回 JSON；此时按
   ``choices[0].message.content`` 解析，不报错。

失败一律抛 ``AIProviderError``（带稳定 ``kind``），绝不吞掉。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.ai.base import AIProvider, AIProviderError, DEFAULT_SYSTEM_PROMPT

__all__ = ["OpenAICompatibleProvider"]

_RETRYABLE_KINDS = {"rate_limited", "timeout", "unreachable", "provider_error"}


class OpenAICompatibleProvider(AIProvider):
    mode = "real"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        name: str = "openai_compatible",
        timeout: float = 60.0,
        stream: bool = True,
        max_retries: int = 1,
        proxy: str = "",
        max_tokens_limit: int | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self.timeout = float(timeout)
        self.stream = bool(stream)
        self.max_retries = max(0, int(max_retries))
        self.proxy = proxy or ""
        self.max_tokens_limit = max_tokens_limit
        self.last_call: dict[str, Any] | None = None
        if not self.base_url or not self.model:
            raise AIProviderError(
                "base_url 与 model 都必须配置", kind="not_configured"
            )

    # -- 内部 ---------------------------------------------------------------

    def _endpoint(self) -> str:
        # base_url 允许两种写法：https://host/v1 或 https://host/v1/chat/completions
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "follow_redirects": True,
            "headers": {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"},
        }
        if self.proxy:
            kwargs["proxies"] = self.proxy
            kwargs["trust_env"] = False
        else:
            # 默认不读环境代理：本机常驻代理客户端（如 FlClash）会接管 127.0.0.1，
            # 造成本地模型端点连不上。需要代理时用 BIDOS_AI_PROXY 显式指定。
            kwargs["trust_env"] = False
        if self._api_key:
            kwargs["headers"]["Authorization"] = f"Bearer {self._api_key}"
        return httpx.Client(**kwargs)

    def _payload(self, system: str, user: str, max_tokens: int | None, temperature: float | None) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system or DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": self.stream}
        if self.stream:
            payload["stream_options"] = {"include_usage": True}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    @staticmethod
    def _classify(status: int) -> str:
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limited"
        if status in (400, 404, 422):
            return "model_or_endpoint"
        if status >= 500:
            return "provider_error"
        return "provider_error"

    # -- 调用 ---------------------------------------------------------------

    def complete(
        self,
        *,
        system: str = DEFAULT_SYSTEM_PROMPT,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> str:
        if timeout:
            self.timeout = float(timeout)
        started = time.monotonic()
        attempts = self.max_retries + 1
        last_error: AIProviderError | None = None

        for attempt in range(attempts):
            try:
                text, usage = self._single_call(system, user, max_tokens, temperature)
                self.last_call = {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "provider": self.name,
                    "model": self.model,
                    "endpoint": self._endpoint(),
                    "status": "ok",
                    "attempts": attempt + 1,
                    "durationMs": int((time.monotonic() - started) * 1000),
                    "chars": len(text or ""),
                    "usage": usage,
                }
                return text
            except AIProviderError as exc:
                last_error = exc
                self.last_call = {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "provider": self.name,
                    "model": self.model,
                    "endpoint": self._endpoint(),
                    "status": "failed",
                    "attempts": attempt + 1,
                    "durationMs": int((time.monotonic() - started) * 1000),
                    "error": exc.as_dict(),
                }
                if exc.kind not in _RETRYABLE_KINDS or attempt + 1 >= attempts:
                    raise
                time.sleep(0.8 * (attempt + 1))
        raise last_error or AIProviderError("调用失败且无异常细节", kind="provider_error")

    def _single_call(
        self,
        system: str,
        user: str,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[str, dict[str, Any] | None]:
        payload = self._payload(system, user, max_tokens, temperature)
        try:
            with self._client() as client:
                if self.stream:
                    return self._read_stream(client, payload)
                response = client.post(self._endpoint(), json=payload)
                return self._read_json(response)
        except httpx.TimeoutException as exc:
            raise AIProviderError(f"模型调用超时（{self.timeout}s）：{exc}", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"无法连接模型端点：{type(exc).__name__}: {exc}", kind="unreachable") from exc

    def _read_stream(self, client: httpx.Client, payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        content: list[str] = []
        usage: dict[str, Any] | None = None
        with client.stream("POST", self._endpoint(), json=payload) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", "replace")[:500]
                raise AIProviderError(
                    f"模型端点返回 HTTP {response.status_code}：{body}",
                    kind=self._classify(response.status_code),
                    detail={"status": response.status_code, "body": body},
                )
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                # 端点忽略了 stream，回退按整体 JSON 解析
                raw = response.read()
                return self._read_json_bytes(raw, detail={"fallback": "endpoint ignored stream"})

            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                else:
                    data = line.strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    err = chunk["error"]
                    raise AIProviderError(
                        f"模型流内错误：{err}", kind="provider_error", detail=err
                    )
                if isinstance(chunk, dict) and chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in (chunk.get("choices") or []) if isinstance(chunk, dict) else []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content.append(piece)
        text = "".join(content)
        if not text.strip():
            raise AIProviderError("模型返回内容为空", kind="bad_response")
        return text, usage

    def _read_json(self, response: httpx.Response) -> tuple[str, dict[str, Any] | None]:
        if response.status_code >= 400:
            body = response.text[:500]
            raise AIProviderError(
                f"模型端点返回 HTTP {response.status_code}：{body}",
                kind=self._classify(response.status_code),
                detail={"status": response.status_code, "body": body},
            )
        return self._read_json_bytes(response.content)

    def _read_json_bytes(self, raw: bytes, detail: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise AIProviderError(f"模型响应不是合法 JSON：{exc}", kind="bad_response", detail=detail) from exc
        choices = payload.get("choices") or []
        if not choices:
            raise AIProviderError("模型响应缺少 choices", kind="bad_response", detail=payload)
        message = choices[0].get("message") or {}
        text = message.get("content") or choices[0].get("text") or ""
        if not str(text).strip():
            raise AIProviderError("模型返回内容为空", kind="bad_response", detail=payload)
        return str(text), payload.get("usage")
