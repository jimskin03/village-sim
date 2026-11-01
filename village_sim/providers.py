"""Optional OpenAI-compatible model calls. Secrets never enter world snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cognition.memory import memory_view
from .cognition.planning import CONTRACT, validate_proposal
from .cognition import conversations, reflection
from .visitors import PROMPT_NOTE, SPEECH_OPEN


class ProviderError(Exception):
    """A safe, user-facing provider failure with no response-body or key details."""


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    every_steps: int = 8
    embedding_model: str | None = None

    @classmethod
    def parse(cls, data: dict, previous: ProviderConfig | None = None):
        base = data.get("base_url", "")
        model = data.get("model", "")
        key = data.get("api_key", "")
        every = data.get("every_steps", 8)
        embedding_model = data.get("embedding_model", previous.embedding_model if previous else None)
        if embedding_model is not None and (not isinstance(embedding_model, str) or len(embedding_model) > 150):
            raise ValueError("Embedding model must be at most 150 characters")
        embedding_model = (embedding_model.strip() or None) if isinstance(embedding_model, str) else None
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
        return cls(base, model.strip(), key, every, embedding_model)


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


def _messages(instructions: str, context: dict) -> list[dict]:
    """System and user messages. Visitor speech stays quoted, untrusted data."""
    content = json.dumps(context, ensure_ascii=False)
    if SPEECH_OPEN in content:
        instructions = instructions + " " + PROMPT_NOTE
    return [{"role": "system", "content": instructions}, {"role": "user", "content": content}]


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
    context = {k: v for k, v in observation.items() if k not in ("agent", "reflection")}
    for key in ("suppers", "reflections"):
        if key in context:
            context[key] = context[key][-3:]
    context["agent"] = {
        k: v for k, v in agent.items() if k not in ("memory", "provider_until")
    }
    context["recent_memories"] = observation.get("recent_memories", agent["memory"][-12:])
    # Vectors are storage/retrieval data, not useful language-model context.
    for key in ("recent_memories", "relevant_memories"):
        context[key] = memory_view(context.get(key, []), characters=3000 if key == "recent_memories" else 6000)
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
    if observation.get("decision_contract") == CONTRACT:
        instructions = (
            "You are an independent villager. Choose a short plan grounded in your own memories, "
            "persona and priorities. You may choose any entry in allowed_actions. "
            "Return only JSON: {\"contract\":\"plan-v1\",\"goal\":\"...\",\"steps\":["
            "{\"action\":\"visit\",\"target\":\"tom\",\"location\":\"shore\","
            "\"duration_minutes\":45,\"reason\":\"...\"}],\"memory_ids\":[42]}. "
            "Copy action/location/target from a legal entry; use null for an absent target. "
            "Use 1–8 steps, 15–180 minutes each in multiples of 15, at most one day total. "
            "Goal and reasons are at most 200 characters. Cite only supplied memory IDs. "
            "Do not invent people, places, resources, commitments or past events. The world "
            "determines movement and work outcomes. Visits are attempts to meet someone, "
            "not agreements on their behalf. trust and preferred_helpers record who has "
            "kept or missed meetings with you. Prefer a reliable helper when you need "
            "someone else. Insights interpret the past and cannot change it. Do not use markdown."
        )
    body = {
        "model": config.model,
        "messages": _messages(instructions, context),
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
        proposal = _parse_decision(_message_text(choice))
        return validate_proposal(proposal, observation) if observation.get("decision_contract") == CONTRACT else validate_slots(proposal, observation)
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


def reflect(config: ProviderConfig, context: dict) -> dict:
    instructions = (
        "Reflect privately on the supplied evidence. Return JSON "
        "{\"contract\":\"reflection-v1\",\"insights\":[{\"text\":\"...\",\"source_ids\":[1],"
        "\"participants\":[\"tom\"],\"preferred_helper\":null,\"task\":null}]}. "
        "Use at most three insights. Each text is 1–240 characters and may only interpret "
        "evidence IDs you were given. participants must appear in those memories. "
        "preferred_helper is null, or one of those participants other than yourself; "
        "task is null unless you name a helper, and then it must be one of the supplied tasks. "
        "Do not invent events, people, supplies, trust numbers, or a new biography. Do not use markdown."
    )
    try:
        payload = _complete(config, {"model": config.model, "messages": _messages(instructions, context)})
        return reflection.validate_insights(_parse_decision(_message_text(payload["choices"][0])), context)
    except HTTPError as error:
        error.close()
        raise ProviderError("The reflection provider rejected the request. A private reflection will be used.") from None
    except (ProviderError, URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError("The reflection provider could not produce a valid reflection. A private reflection will be used.") from None


def converse(config: ProviderConfig, context: dict) -> dict:
    instructions = (
        "Speak as this villager using only your own supplied memories and the shared transcript. "
        "Consider your goals, daily plan and accepted commitments independently. Negotiate a meeting "
        "with propose, counter, accept or decline; use say for ordinary conversation. "
        "Return JSON with contract 'conversation-v1', the supplied conversation_id and turn, move, "
        "speech (1–240 characters), and terms (null for say/decline). Terms must contain participants "
        "in the supplied order, place, task, start_minute (absolute game minutes), duration_minutes. "
        "Use a listed task/place, 15–180 minutes in multiples of 15, within the next day. "
        "Allow travel time. Talk between 06:00 and 23:00; craft work between 06:00 and 18:00. "
        "Acceptance must repeat exactly the latest partner offer. Counter to change any term. "
        "Speech alone never creates a commitment. Do not invent past events or agree for your partner."
    )
    try:
        payload = _complete(config, {"model": config.model, "messages": _messages(instructions, context)})
        return conversations.validate_turn(_parse_decision(_message_text(payload["choices"][0])), context)
    except HTTPError as error:
        error.close()
        raise ProviderError("The conversation provider rejected the request. A rule-based reply will be used.") from None
    except (ProviderError, URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError("The conversation provider could not produce a valid turn. A rule-based reply will be used.") from None


def _complete(config: ProviderConfig, body: dict, endpoint="/chat/completions", max_bytes=256 * 1024) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    request = Request(
        config.base_url + endpoint,
        data=json.dumps(body).encode(),
        headers=headers,
    )
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
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
