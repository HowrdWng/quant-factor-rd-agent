from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError


class ModelProviderError(RuntimeError):
    pass


TModel = TypeVar("TModel", bound=BaseModel)


class ModelProvider:
    provider_name: str
    model_name: str

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        temperature: float = 0.2,
    ) -> TModel:
        raise NotImplementedError


class LLMHealthCheck(BaseModel):
    status: str = Field(min_length=2)
    message: str = Field(min_length=2)


@dataclass(frozen=True)
class OpenAICompatibleProvider(ModelProvider):
    api_key: str
    model_name: str
    base_url: str = "https://api.openai.com/v1"
    provider_name: str = "openai_compatible"
    api_style: str = "auto"
    timeout_seconds: int = 90

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        temperature: float = 0.2,
    ) -> TModel:
        errors: list[str] = []
        for style in self._styles_to_try():
            try:
                raw = self._request(
                    style,
                    system=system,
                    user=user,
                    schema=schema,
                    temperature=temperature,
                )
                content = self._extract_content(style, raw)
                return self._validate_content(content, schema)
            except ModelProviderError as exc:
                errors.append(f"{style}: {exc}")
        raise ModelProviderError("All LLM API styles failed. " + " | ".join(errors))

    def health_check(self) -> LLMHealthCheck:
        return self.generate_structured(
            system="Return a tiny JSON health check. Return only JSON.",
            user='Return {"status":"ok","message":"model reachable"}',
            schema=LLMHealthCheck,
        )

    def _styles_to_try(self) -> list[str]:
        style = self.api_style.strip().lower()
        if style in {"responses", "chat"}:
            return [style]
        if "api.openai.com" in self.base_url:
            return ["responses", "chat"]
        return ["chat", "responses"]

    def _request(
        self,
        style: str,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        temperature: float,
    ) -> dict[str, Any]:
        if style == "responses":
            endpoint = "responses"
            payload = self._responses_payload(
                system=system,
                user=user,
                schema=schema,
                temperature=temperature,
            )
        elif style == "chat":
            endpoint = "chat/completions"
            payload = self._chat_payload(
                system=system,
                user=user,
                schema=schema,
                temperature=temperature,
            )
        else:
            raise ModelProviderError(f"Unsupported API style '{style}'")

        raw = self._post(endpoint, payload)
        # Successful Responses API payloads can include ``"error": null``.
        # Only a non-null error object represents an API failure.
        if raw.get("error"):
            raise ModelProviderError(str(raw["error"]))
        return raw

    def _responses_payload(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user}],
                },
            ],
            "text": {"format": self._response_format(schema)},
            "temperature": self._temperature(temperature),
        }
        return payload

    def _chat_payload(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": self._chat_response_format(schema),
            "temperature": self._temperature(temperature),
        }
        return payload

    def _temperature(self, requested: float) -> float:
        configured = os.getenv("LLM_TEMPERATURE", "").strip()
        return float(configured) if configured else requested

    def _supports_json_schema(self) -> bool:
        return self.provider_name == "openai" or "api.openai.com" in self.base_url.lower()

    @staticmethod
    def _schema_config(schema: type[BaseModel]) -> dict[str, Any]:
        return {
            "name": schema.__name__[:64],
            "strict": False,
            "schema": schema.model_json_schema(),
        }

    def _response_format(self, schema: type[BaseModel]) -> dict[str, Any]:
        if self._supports_json_schema():
            return {"type": "json_schema", **self._schema_config(schema)}
        return {"type": "json_object"}

    def _chat_response_format(self, schema: type[BaseModel]) -> dict[str, Any]:
        if self._supports_json_schema():
            return {"type": "json_schema", "json_schema": self._schema_config(schema)}
        return {"type": "json_object"}

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelProviderError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelProviderError(f"LLM request failed: {exc}") from exc

        return raw

    def _extract_content(self, style: str, raw: dict[str, Any]) -> str:
        if style == "chat":
            content = raw.get("choices", [{}])[0].get("message", {}).get("content")
            if isinstance(content, str):
                return content
            raise ModelProviderError("chat response did not contain message content")

        if isinstance(raw.get("output_text"), str):
            return str(raw["output_text"])
        output = raw.get("output")
        if isinstance(output, list):
            pieces: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        pieces.append(part["text"])
            if pieces:
                return "\n".join(pieces)
        raise ModelProviderError("responses API response did not contain output text")

    def _validate_content(self, content: str, schema: type[TModel]) -> TModel:
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("LLM response content was empty")
        try:
            data: dict[str, Any] = json.loads(_clean_json_content(content))
        except json.JSONDecodeError as exc:
            raise ModelProviderError(f"LLM returned non-JSON content: {content[:500]}") from exc
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise ModelProviderError(f"LLM JSON did not match {schema.__name__}: {exc}") from exc


def _clean_json_content(content: str) -> str:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return match.group(0)
    return text


def provider_from_env() -> ModelProvider:
    return provider_from_settings(
        provider=os.getenv("LLM_PROVIDER", "mock"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        api_style=os.getenv("LLM_API_STYLE", "auto"),
    )


def provider_from_settings(
    *,
    provider: str,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    api_style: str = "auto",
) -> ModelProvider:
    provider = provider.strip().lower()
    if provider in {"mock", "offline", ""}:
        raise ModelProviderError("LLM_PROVIDER is mock; no live model provider is configured")
    if provider not in {"openai", "openai_compatible"}:
        raise ModelProviderError(f"Unsupported LLM_PROVIDER '{provider}'")

    api_key = api_key.strip()
    model = model.strip()
    base_url = base_url.strip()
    api_style = api_style.strip() or "auto"
    if not api_key:
        raise ModelProviderError("LLM_API_KEY is required when AGENT_MODE=live")
    if not model:
        raise ModelProviderError("LLM_MODEL is required when AGENT_MODE=live")
    return OpenAICompatibleProvider(
        api_key=api_key,
        model_name=model,
        base_url=base_url,
        provider_name=provider,
        api_style=api_style,
    )
