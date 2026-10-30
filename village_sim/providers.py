"""Optional OpenAI-compatible model calls. Secrets never enter world snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
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


def plan(config: ProviderConfig, observation: dict) -> dict:
    agent = observation["agent"]
    # Memory remains complete in the simulation. Only a bounded view is sent to
    # the model so a long village session does not create ever-larger prompts.
    context = {k: v for k, v in observation.items() if k != "agent"}
    context["agent"] = {
        k: v for k, v in agent.items() if k not in ("memory", "provider_until")
    }
    context["recent_memories"] = agent["memory"][-12:]
    instructions = (
        "You inhabit a villager in a small fishing village. Act according to your persona, "
        "memories, relationships, daily plan, time and nearby people. Choose one believable "
        "activity for the next few game hours. Respect storms, sleep and other villagers. "
        "Return only a JSON object with place (an available place id), activity (1-100 "
        "characters), and speech (optional, at most 200 characters, or null). Do not use "
        "markdown. Do not invent place IDs. Use natural, brief dialogue."
    )
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    request = Request(
        config.base_url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ProviderError(
                "Provider response was too large. Rules are keeping the villager moving."
            )
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError
        # Many compatible local models wrap otherwise valid JSON in fences.
        content = content.strip()
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        action = json.loads(content)
        if not isinstance(action, dict):
            raise ValueError
        return action
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
