from __future__ import annotations

import http.client
import json
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from app.core.config import settings


PLACEHOLDER_KEYS = {"", "your_key_here", "changeme", "replace_me"}
RETRYABLE_LLM_EXCEPTIONS = (
    urllib.error.URLError,
    http.client.HTTPException,
    TimeoutError,
    socket.timeout,
    ConnectionError,
    OSError,
)
RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def is_llm_configured() -> bool:
    api_key = (settings.llm_api_key or "").strip()
    return bool(api_key) and api_key.lower() not in PLACEHOLDER_KEYS


def is_vision_configured() -> bool:
    api_key = (settings.vision_api_key or "").strip()
    return bool(api_key) and api_key.lower() not in PLACEHOLDER_KEYS


def _chat_completions_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    return url


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_code_fence(text.strip())
    parsed = _loads_object(cleaned)
    if parsed is not None:
        return parsed

    for candidate in _balanced_json_candidates(cleaned):
        parsed = _loads_object(candidate)
        if parsed is not None:
            return parsed
    return None


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _balanced_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    starts = [index for index, char in enumerate(text) if char == "{"]
    for start in starts[:12]:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    return candidates


def _post_chat_completions(
    *,
    url: str,
    payload: dict[str, Any],
    api_key: str | None,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    if not api_key:
        return None, "LLM_API_KEY is not configured."

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: str | None = None
    max_attempts = max(1, min(settings.llm_max_retries + 1, 8))
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
                "User-Agent": "TravelShotAgent/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8")), None
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code not in RETRYABLE_HTTP_STATUS or attempt >= max_attempts:
                return None, last_error
            _sleep_before_retry(attempt, exc.headers.get("Retry-After"))
        except json.JSONDecodeError as exc:
            last_error = f"LLM provider returned non-JSON HTTP body: {exc}"
            if attempt >= max_attempts:
                return None, last_error
            _sleep_before_retry(attempt)
        except RETRYABLE_LLM_EXCEPTIONS as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            if attempt < max_attempts:
                _sleep_before_retry(attempt)

    return None, f"temporary network/provider error after {max_attempts} attempts: {last_error or 'unknown error'}"


def _sleep_before_retry(attempt: int, retry_after: str | None = None) -> None:
    delay = _retry_after_delay(retry_after)
    if delay is None:
        base_delay = max(settings.llm_retry_base_delay_seconds, 0.1)
        delay = min(base_delay * (2 ** (attempt - 1)), 8.0)
        delay += random.uniform(0, min(delay * 0.25, 0.75))
    time.sleep(delay)


def _retry_after_delay(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), 8.0)
    except ValueError:
        return None


def complete_json(system_prompt: str, user_prompt: str) -> tuple[dict[str, Any] | None, str | None]:
    if not is_llm_configured():
        return None, None

    url = _chat_completions_url(settings.llm_base_url)
    payload = _json_payload(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return _complete_and_parse(
        url=url,
        payload=payload,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        label="LLM",
    )


def complete_json_multimodal(
    system_prompt: str,
    user_prompt: str,
    image_urls: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    images = [
        image
        for image in (image_urls or [])
        if isinstance(image, str) and image.startswith("data:image/")
    ][:3]
    if not images:
        return complete_json(system_prompt, user_prompt)

    if not is_vision_configured():
        text_result, text_warning = complete_json(system_prompt, user_prompt)
        warning = "VISION_API_KEY is not configured; reference images were not analyzed. "
        if text_warning:
            warning = f"{warning}{text_warning}"
        return text_result, warning

    url = _chat_completions_url(settings.vision_base_url)
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
    payload = _json_payload(
        model=settings.vision_model,
        max_tokens=settings.vision_max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    )
    return _complete_and_parse(
        url=url,
        payload=payload,
        api_key=settings.vision_api_key,
        timeout_seconds=settings.vision_timeout_seconds,
        model=settings.vision_model,
        max_tokens=settings.vision_max_tokens,
        label="Vision LLM",
    )


def _json_payload(*, model: str, messages: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }


def _complete_and_parse(
    *,
    url: str,
    payload: dict[str, Any],
    api_key: str | None,
    timeout_seconds: float,
    model: str,
    max_tokens: int,
    label: str,
) -> tuple[dict[str, Any] | None, str | None]:
    data, error = _post_chat_completions(
        url=url,
        payload=payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    if error:
        return None, f"{label} call failed: {error}"
    if data is None:
        return None, f"{label} call failed: provider returned no content."

    content, finish_reason = _choice_content(data)
    parsed = _extract_json_object(content)
    if parsed is not None:
        return parsed, None

    repaired, repair_warning = _repair_json_response(
        url=url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        model=model,
        max_tokens=max_tokens,
        label=label,
        invalid_content=content,
        finish_reason=finish_reason,
    )
    return repaired, repair_warning


def _choice_content(data: dict[str, Any]) -> tuple[str, str | None]:
    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        text_parts = [item.get("text") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
        content = "\n".join(text_parts)
    return str(content), choice.get("finish_reason")


def _repair_json_response(
    *,
    url: str,
    api_key: str | None,
    timeout_seconds: float,
    model: str,
    max_tokens: int,
    label: str,
    invalid_content: str,
    finish_reason: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if finish_reason == "length":
        return None, f"{label} returned truncated JSON. Increase LLM_MAX_TOKENS/VISION_MAX_TOKENS or reduce prompt size."

    repair_payload = _json_payload(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "system",
                "content": (
                    "You repair malformed model output into one valid JSON object. "
                    "Return JSON only. Do not add explanations. Do not invent missing facts."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Convert this output into a valid JSON object only.",
                        "invalid_output": invalid_content[:12000],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    data, error = _post_chat_completions(
        url=url,
        payload=repair_payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    if error:
        return None, f"{label} returned invalid JSON, and JSON repair retry failed: {error}"

    repaired_content, repaired_finish_reason = _choice_content(data or {})
    parsed = _extract_json_object(repaired_content)
    if parsed is not None:
        return parsed, f"{label} returned invalid JSON once; repaired with one retry."

    preview = invalid_content.replace("\n", " ")[:240]
    suffix = " The output may have been truncated." if repaired_finish_reason == "length" else ""
    return None, f"{label} returned content that is not parseable JSON after repair retry.{suffix} Preview: {preview}"
