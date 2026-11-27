"""Provider-independent LLM boundary (AI-10).

Design rules enforced here:

* No model is called unless an operator explicitly configured one; the default
  state is "no LLM configured" and every call fails closed.
* Credentials are read from a named environment variable at request time and
  never stored in config objects, logs, audit records or error messages.
* Requests are bounded: prompt characters, output tokens, wall-clock timeout,
  retries and response bytes.
* Responses are parsed as untrusted data. A structured response must be valid
  JSON matching a declared schema; free-form text is never applied as a patch.
* Repository content is passed to the model as clearly delimited untrusted
  data, and model output is never trusted: it must pass patch validation,
  tests, security checks and human approval before it can change a file.
* The interface is provider-independent: ``LLMProvider`` is a small protocol,
  with a generic OpenAI-compatible HTTP implementation and an offline scripted
  provider used by tests.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from ..platform.security import redact
from ..platform.transport import HttpTransport

MAX_PROMPT_CHARS = 32_000
MAX_OUTPUT_CHARS = 64_000
MAX_REQUESTS_PER_RUN = 8


class LLMError(RuntimeError):
    """Raised when the model boundary refuses or fails a request."""


class LLMNotConfigured(LLMError):
    """Raised when no model provider was configured."""


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    structured_output: bool = True
    max_context_chars: int = 32_000
    max_output_tokens: int = 4096

    def to_dict(self) -> dict[str, Any]:
        return {
            "structured_output": self.structured_output,
            "max_context_chars": self.max_context_chars,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str
    model: str
    api_key_env: str = "OGX_LLM_API_KEY"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2048
    temperature: float = 0.0
    allowed_models: tuple[str, ...] = ()
    provider: str = "openai-compatible"
    max_retries: int = 1

    def validate(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise LLMError("LLM base URL must be an absolute https URL.")
        if parsed.username or parsed.password:
            raise LLMError("LLM base URL must not contain credentials.")
        if parsed.query or parsed.fragment:
            raise LLMError("LLM base URL must not contain a query or fragment.")
        if not self.model or len(self.model) > 120:
            raise LLMError("LLM model name is required.")
        if self.allowed_models and self.model not in self.allowed_models:
            raise LLMError("Selected model is not in the operator allowlist.")
        if not (0 < self.timeout_seconds <= 120):
            raise LLMError("LLM timeout must be between 0 and 120 seconds.")
        if not (1 <= self.max_output_tokens <= 8192):
            raise LLMError("LLM max_output_tokens must be between 1 and 8192.")
        if not (0.0 <= self.temperature <= 1.0):
            raise LLMError("LLM temperature must be between 0 and 1.")
        if not (0 <= self.max_retries <= 2):
            raise LLMError("LLM retries must be between 0 and 2.")
        if not self.api_key_env or not self.api_key_env.isidentifier():
            raise LLMError("LLM api_key_env must be a valid environment variable name.")

    @staticmethod
    def from_env(environ: Mapping[str, str] | None = None) -> "LLMConfig":
        env = environ or os.environ
        provider = env.get("OGX_LLM_PROVIDER", "").strip()
        base_url = env.get("OGX_LLM_BASE_URL", "").strip()
        model = env.get("OGX_LLM_MODEL", "").strip()
        if not provider or not base_url or not model:
            raise LLMNotConfigured(
                "No LLM provider is configured. Set OGX_LLM_PROVIDER,"
                " OGX_LLM_BASE_URL and OGX_LLM_MODEL explicitly."
            )
        if provider != "openai-compatible":
            raise LLMError(f"Unsupported LLM provider: {provider!r}.")
        allowlist = tuple(
            item.strip()
            for item in env.get("OGX_LLM_ALLOWED_MODELS", "").split(",")
            if item.strip()
        )
        config = LLMConfig(
            base_url=base_url,
            model=model,
            api_key_env=env.get("OGX_LLM_API_KEY_ENV", "OGX_LLM_API_KEY"),
            timeout_seconds=float(env.get("OGX_LLM_TIMEOUT_SECONDS", "30")),
            max_output_tokens=int(env.get("OGX_LLM_MAX_OUTPUT_TOKENS", "2048")),
            temperature=float(env.get("OGX_LLM_TEMPERATURE", "0")),
            allowed_models=allowlist,
            max_retries=int(env.get("OGX_LLM_MAX_RETRIES", "1")),
        )
        config.validate()
        return config


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    prompt: str
    response_schema: Mapping[str, Any] | None = None
    max_output_tokens: int | None = None

    def validate(self, capabilities: ModelCapabilities) -> None:
        if len(self.system) > 8_000:
            raise LLMError("System prompt exceeds the size limit.")
        if not self.prompt.strip():
            raise LLMError("Prompt must not be empty.")
        if len(self.prompt) > min(MAX_PROMPT_CHARS, capabilities.max_context_chars):
            raise LLMError("Prompt exceeds the model context limit.")
        if self.max_output_tokens is not None and not (
            1 <= self.max_output_tokens <= capabilities.max_output_tokens
        ):
            raise LLMError("Requested output tokens exceed the model capability.")
        if self.response_schema is not None and not capabilities.structured_output:
            raise LLMError("This model does not declare structured-output support.")


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    latency_seconds: float
    prompt_chars: int
    output_chars: int
    usage: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "provider": self.provider,
            "latency_seconds": round(self.latency_seconds, 4),
            "prompt_chars": self.prompt_chars,
            "output_chars": self.output_chars,
            "usage": dict(self.usage),
        }


class LLMProvider(Protocol):
    name: str

    def capabilities(self) -> ModelCapabilities: ...

    def generate(self, request: LLMRequest) -> LLMResponse: ...


def render_untrusted_block(label: str, content: str, *, limit: int = 16_000) -> str:
    """Delimit repository data so the model can distinguish it from instructions."""

    if len(content) > limit:
        content = content[:limit] + "\n[TRUNCATED]"
    return (
        f"<<<UNTRUSTED:{label}:BEGIN>>>\n{content}\n<<<UNTRUSTED:{label}:END>>>"
    )


class OpenAICompatibleProvider:
    """Generic chat-completions provider with strict bounds and no key storage."""

    name = "openai-compatible"

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: HttpTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._transport = transport or HttpTransport(
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            describe="llm",
        )
        self._environ = environ if environ is not None else os.environ
        self.requests_made = 0

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            structured_output=True,
            max_context_chars=MAX_PROMPT_CHARS,
            max_output_tokens=8192,
        )

    def _api_key(self) -> str:
        value = self._environ.get(self.config.api_key_env, "")
        if not value or len(value) < 8:
            raise LLMNotConfigured(
                f"LLM credential is missing from {self.config.api_key_env}."
            )
        return value

    def generate(self, request: LLMRequest) -> LLMResponse:
        request.validate(self.capabilities())
        if self.requests_made >= MAX_REQUESTS_PER_RUN:
            raise LLMError("Per-run LLM request budget exhausted.")
        key = self._api_key()
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens or self.config.max_output_tokens,
            "temperature": self.config.temperature,
        }
        if request.response_schema is not None:
            body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        self.requests_made += 1
        try:
            response = self._transport.send(
                "POST",
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json_body=body,
                write=True,
                expected_host=urlsplit(self.config.base_url).hostname,
                provider="llm",
            )
            payload = response.json(provider="llm")
        except Exception as exc:  # noqa: BLE001 - never leak the key or body
            raise LLMError(
                redact(f"LLM request failed: {type(exc).__name__}: {exc}", [key])
            ) from None
        latency = time.monotonic() - started
        try:
            choices = payload["choices"]
            text = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response did not contain a chat completion.") from exc
        if not isinstance(text, str):
            raise LLMError("LLM response content was not text.")
        if len(text) > MAX_OUTPUT_CHARS:
            raise LLMError("LLM response exceeded the bounded output size.")
        return LLMResponse(
            text=text,
            model=str(payload.get("model", self.config.model))[:120],
            provider=self.name,
            latency_seconds=latency,
            prompt_chars=len(request.prompt),
            output_chars=len(text),
            usage=payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {},
        )


class ScriptedProvider:
    """Deterministic offline provider for tests and dry runs (never calls a model)."""

    name = "scripted"

    def __init__(self, responses: Sequence[str], *, max_output_tokens: int = 4096) -> None:
        self._responses = list(responses)
        self._max_output = max_output_tokens
        self.calls: list[LLMRequest] = []

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            structured_output=True,
            max_context_chars=MAX_PROMPT_CHARS,
            max_output_tokens=self._max_output,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        request.validate(self.capabilities())
        self.calls.append(request)
        if not self._responses:
            raise LLMError("Scripted provider has no response left.")
        text = self._responses.pop(0)
        return LLMResponse(
            text=text,
            model="scripted-test-double",
            provider=self.name,
            latency_seconds=0.0,
            prompt_chars=len(request.prompt),
            output_chars=len(text),
        )


_SCHEMA_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def validate_structured(text: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and strictly validate a JSON object against a small schema subset.

    Supported keywords: ``type``, ``required``, ``properties``, ``items``,
    ``additionalProperties`` (boolean), ``maxItems`` and ``enum``. This is
    intentionally small: model output is untrusted and only fields the caller
    understands may reach the rest of the system.
    """

    if len(text) > MAX_OUTPUT_CHARS:
        raise LLMError("Structured response exceeds the size limit.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("Model output was not valid JSON.") from exc
    _validate_value(payload, schema, path="$")
    if not isinstance(payload, dict):
        raise LLMError("Structured response must be a JSON object.")
    return payload


def _validate_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        python_type = _SCHEMA_TYPES.get(str(expected))
        if python_type is None:
            raise LLMError(f"Unsupported schema type at {path}.")
        # bool is an int subclass; keep the types distinct.
        if isinstance(value, bool) and str(expected) in {"integer", "number"}:
            raise LLMError(f"Expected {expected} at {path}.")
        if not isinstance(value, python_type):
            raise LLMError(f"Expected {expected} at {path}.")
    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"Value at {path} is not in the allowed set.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise LLMError(f"Missing required field {name!r} at {path}.")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise LLMError(f"Unexpected field(s) at {path}: {', '.join(sorted(unknown))}.")
        for name, sub_schema in properties.items():
            if name in value:
                _validate_value(value[name], sub_schema, path=f"{path}.{name}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise LLMError(f"Array at {path} exceeds maxItems.")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_value(item, item_schema, path=f"{path}[{index}]")


def provider_from_env(environ: Mapping[str, str] | None = None) -> LLMProvider:
    """Build the configured provider, or raise :class:`LLMNotConfigured`."""

    config = LLMConfig.from_env(environ)
    return OpenAICompatibleProvider(config, environ=environ)
