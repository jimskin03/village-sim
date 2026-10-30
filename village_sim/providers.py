"""Optional OpenAI-compatible model calls. Secrets never enter world snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderError(Exception):
    """A safe, user-facing provider failure with no response-body or key details."""


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    every_steps: int = 8

    @classmethod
    def parse(cls, data: dict, previous: ProviderConfig | None = None):
        base = data.get("base_url", "")
        model = data.get("model", "")
        key = data.get("api_key", "")
        every = data.get("every_steps", 8)
        if not isinstance(base, str) or len(base) > 500:
            raise ValueError("Enter an API base URL")
        base = base.strip().rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in base)
        ):
            raise ValueError(
                "Use an HTTP(S) API base URL without credentials or query parameters"
            )
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError(
                "Use HTTPS for a remote provider, or HTTP for a local server"
            )
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        if not isinstance(model, str) or not 1 <= len(model.strip()) <= 150:
            raise ValueError("Enter your provider's model ID (1–150 characters)")
        if not isinstance(key, str) or len(key) > 2048 or any(c in key for c in "\r\n"):
            raise ValueError("Enter a valid API key")
        if type(every) is not int or every not in (4, 8, 16, 32):
            raise ValueError("Choose 4, 8, 16 or 32 steps between decisions")
        key = key.strip()
        if not key and previous and previous.base_url == base:
            key = previous.api_key
        return cls(base, model.strip(), key, every)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_DECISION_KEYS = {"place", "activity", "result", "mood", "promise", "speech", "thought"}


def _message_text(choice: dict) -> str:
    message = choice.get("message", {})
    if not isinstance(message, dict):
        message = {"content": message}
    chunks = []

    def add(value):
        if isinstance(value, str) and value.strip():
            chunks.append(value)
        elif isinstance(value, list):
            for part in value:
                if isinstance(part, str):
                    add(part)
                elif isinstance(part, dict):
                    add(part.get("text", part.get("content")))

    add(message.get("content"))
    for key in ("reasoning", "reasoning_content"):
        add(message.get(key))
        add(choice.get(key))
    if not chunks:
        raise ValueError("The model returned no text")
    return "\n".join(chunks)


def _loads_object(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    for attempt in (text, _TRAILING_COMMA.sub(r"\1", text)):
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        if isinstance(value, dict):
            if _DECISION_KEYS & set(value):
                return value
            nested = [item for item in value.values() if isinstance(item, dict)]
            if len(nested) == 1 and _DECISION_KEYS & set(nested[0]):
                return nested[0]
            return value
    return None


def _parse_decision(text: str) -> dict:
    text = _THINKING.sub("", text).strip()
    candidates = [match.group(1) for match in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    candidates.append(text)
    for candidate in candidates:
        action = _loads_object(candidate)
        if action is not None:
            return action
    raise ValueError("The model response did not contain a JSON decision")


def _offered(value: str, options: list) -> str | None:
    if value in options:
        return value
    folded = _plain(value).casefold()
    for option in options:
        if isinstance(option, str) and _plain(option).casefold() == folded:
            return option
    return None


def _plain(value: str) -> str:
    return (
        value.strip()
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
    )


def plan(config: ProviderConfig, observation: dict) -> dict:
    agent = observation["agent"]
    # Memory remains complete in the simulation. Only a bounded view is sent to
    # the model so a long village session does not create ever-larger prompts.
    context = {k: v for k, v in observation.items() if k != "agent"}
    for key in ("suppers", "reflections"):
        if key in context:
            context[key] = context[key][-3:]
    context["agent"] = {
        k: v for k, v in agent.items() if k not in ("memory", "provider_until")
    }
    context["recent_memories"] = agent["memory"][-12:]
    instructions = (
        "You inhabit a villager in a small fishing village. Act according to your persona, "
        "memories, relationships, daily goals, daily plan, time and nearby people. "
        "The rules already choose the activity and destination in suggested_action. "
        "Do not invent or change activities, places, supplies or past events. "
        "Omit place and activity, or copy them exactly. Any other place is ignored and the "
        "rules move the villager. Fill these optional slots: result (one exact ID from "
        "result_options), mood (one exact value from mood_options), promise (one exact sentence "
        "from promise_options), speech (one exact line from speech_options), thought (a brief "
        "thought grounded in the supplied facts, at most 200 characters). Choose null when a "
        "slot has no suitable option. Return only a JSON object with these slots. Do not use markdown."
    )
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        try:
            payload = _complete(config, body)
        except HTTPError as error:
            status = error.code
            error.close()
            if status != 400:
                raise ProviderError(
                    f"Provider returned HTTP {status}. Check the endpoint, model, API key and account limits."
                ) from None
            # Some local models reject JSON mode. Ask once more in plain text.
            payload = _complete(config, {k: v for k, v in body.items() if k != "response_format"})
        choice = payload["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError
        return validate_slots(_parse_decision(_message_text(choice)), observation)
    except ProviderError:
        raise
    except HTTPError as error:
        status = error.code
        error.close()
        raise ProviderError(
            f"Provider returned HTTP {status}. Check the endpoint, model, API key and account limits."
        ) from None
    except (URLError, OSError, TimeoutError):
        raise ProviderError(
            "Could not reach the provider within 30 seconds. Check the endpoint and connection."
        ) from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError(
            "The model did not return a valid decision. Try a model that follows JSON instructions."
        ) from None


def _complete(config: ProviderConfig, body: dict) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    request = Request(
        config.base_url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ProviderError(
            "Provider response was too large. Rules are keeping the villager moving."
        )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError
    return payload


def validate_slots(action: dict, observation: dict) -> dict:
    """Keep offered slots. A wrong place or activity is dropped; the rules supply those."""
    slots = ("result", "mood", "promise", "speech", "thought")
    suggested = observation["suggested_action"]
    result = {key: suggested[key] for key in ("place", "activity")}
    for key in slots:
        value = action.get(key)
        if not isinstance(value, str):
            result[key] = None
            continue
        if key == "thought":
            value = value.strip()[:200]
            result[key] = value or None
            continue
        result[key] = _offered(value, observation.get(key + "_options", []))
    return result
