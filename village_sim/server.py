"""One local process serving static UI, SSE snapshots and optional agent actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import mimetypes
from pathlib import Path
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Thread
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse, urlsplit

from . import providers, visitors
from .cognition.planning import CONTRACT
from .cognition.embeddings import EmbeddingCache
from .cognition import conversations, reflection
from .simulation import Simulation
from .persistence import export_session, read_session, restore_session, write_session
from .recording import RecordingArchive

STATIC = Path(__file__).parent / "static"
OPERATOR_HEADER = "X-Operator-Token"
OPERATOR_ENV = "VILLAGE_SIM_OPERATOR_TOKEN"
INVITE_ENV = "VILLAGE_SIM_VISITOR_INVITE"
# Endpoints that change the whole village. When an operator token is required
# they need it in addition to the same-origin local check.
OPERATOR_PATHS = ("/api/control", "/api/events", "/api/session/resume", "/api/visitors/control")


class OperatorRequired(PermissionError):
    """The operator token is missing or wrong."""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _split_host(value: str) -> tuple[str, str]:
    """Return a lowercase hostname and port from a Host header or address."""
    value = value.strip()
    if value.startswith("["):
        name, separator, rest = value[1:].partition("]")
        if not separator:
            return "", ""
        return name.lower(), rest[1:] if rest.startswith(":") else ""
    if value.count(":") > 1:
        return value.lower(), ""
    if ":" in value:
        name, port = value.rsplit(":", 1)
        if port.isdigit():
            return name.lower().rstrip("."), port
    return value.lower().rstrip("."), ""


def _loopback(name: str) -> bool:
    name = name.lower().strip("[]").split("%", 1)[0]
    if name.startswith("::ffff:"):
        name = name.removeprefix("::ffff:")
    return name == "localhost" or name == "::1" or name.startswith("127.")


@dataclass
class Brain:
    config: providers.ProviderConfig
    status: str = "waiting"
    error: str | None = None
    requests: int = 0
    decisions: int = 0
    next_step: int = 0
    next_call: float = 0
    action: dict | None = None
    until: int = 0
    speak: bool = False
    force: bool = False
    embedding_cache: EmbeddingCache = field(default_factory=EmbeddingCache, repr=False)
    embedding_error: str | None = None

    def public(self):
        return {
            "base_url": self.config.base_url,
            "model": self.config.model,
            "every_steps": self.config.every_steps,
            "embedding_model": self.config.embedding_model,
            "embedding_error": self.embedding_error,
            "has_key": bool(self.config.api_key),
            "status": self.status,
            "error": self.error,
            "requests": self.requests,
            "decisions": self.decisions,
        }


class Session:
    def __init__(self, count=3, seed=7, interval=2.0, state_path=None, cognition_mode="rules", *,
                 visitors_enabled=False, operator_token=None, invite_code=None, public_bind=False):
        self.condition = Condition()
        if operator_token is not None and (not isinstance(operator_token, str) or len(operator_token) < 16
                                           or any(c.isspace() for c in operator_token)):
            raise ValueError("The operator token must be at least 16 characters without spaces")
        if invite_code is not None and (not isinstance(invite_code, str) or not invite_code.strip()):
            raise ValueError("The visitor invite code must not be empty")
        # Secrets live only in server memory: never in state, checkpoints or replays.
        self.operator_token = operator_token or secrets.token_urlsafe(24)
        self.operator_token_explicit = operator_token is not None
        self.operator_announced = False
        self.invite_code = invite_code
        self.public_bind = public_bind
        self.visitors_enabled = bool(visitors_enabled)
        self.visitor_tokens: dict[str, str] = {}
        self.state_path = Path(state_path) if state_path else None
        saved = read_session(self.state_path) if self.state_path and self.state_path.exists() else None
        self.sim = restore_session(saved) if saved is not None else Simulation(count, seed, cognition_mode=cognition_mode)
        settings = saved.get("settings", {}) if saved else {}
        self.interval = settings.get("interval", interval)
        if type(self.interval) not in (int, float) or self.interval <= 0:
            raise ValueError("Invalid saved interval")
        self.paused = settings.get("paused", False)
        if type(self.paused) is not bool:
            raise ValueError("Invalid saved playback status")
        # Visitor tokens do not survive a restart, so visitors who were present leave.
        visitors.depart_all(self.sim, "revoked", "left when the village restarted")
        self.persistence_error = None
        self.running = True
        self.revision = 0
        self.tokens: dict[str, str] = {}
        self.brains: dict[str, Brain] = {}
        self.workers: set[str] = set()
        self.archive = RecordingArchive(self.state_path, saved.get("recording") if saved else None)
        if not self.archive.count:
            self.archive.append(self.sim.snapshot())
        self.recording = self.archive.recent()
        self.thread = Thread(target=self._run, daemon=True)
        self.persist()

    def state(self):
        return {
            **self.sim.snapshot(),
            "paused": self.paused,
            "interval": self.interval,
            "revision": self.revision,
            "brains": {aid: brain.public() for aid, brain in self.brains.items()},
            "visitors_enabled": self.visitors_enabled,
            "visitor_invite_required": self.invite_code is not None,
            "operator_required": self.operator_required,
            "persistence_error": ("The replay archive could not be written completely. Check disk space before starting a new story."
                                  if self.archive.failed else self.persistence_error),
        }

    @property
    def operator_required(self):
        """Local single-user runs keep working without a token. Opening the
        village to visitors, binding beyond loopback, or setting a token turns
        operator authentication on."""
        return self.visitors_enabled or self.public_bind or self.operator_token_explicit

    def announce_operator(self):
        """Print a generated operator token once, to the local console only."""
        if self.operator_announced or self.operator_token_explicit or not self.operator_required:
            return
        self.operator_announced = True
        print("Operator token (keep private; send it as the " + OPERATOR_HEADER
              + " header or enter it in the viewer): " + self.operator_token, flush=True)

    def operator_ok(self, supplied):
        if not self.operator_required:
            return True
        return isinstance(supplied, str) and hmac.compare_digest(
            supplied.encode(), self.operator_token.encode())

    def visitor_ok(self, vid, header):
        digest = self.visitor_tokens.get(vid)
        visitor = self.sim.visiting["visitors"].get(vid)
        if not (digest and visitor and visitor["status"] == "active" and header.startswith("Bearer ")):
            return False
        return hmac.compare_digest(_digest(header[7:]), digest)

    def register_visitor(self, data):
        if not self.visitors_enabled:
            raise visitors.Unavailable("Lantern Bay is not accepting visitors right now")
        invite_ok = self.invite_code is None or (
            isinstance(data.get("invite"), str)
            and hmac.compare_digest(data["invite"].encode(), self.invite_code.encode()))
        visitor = visitors.register(self.sim, data, invite_ok=invite_ok)
        token = secrets.token_urlsafe(32)
        self.visitor_tokens[visitor["id"]] = _digest(token)
        self.publish(record=False)
        vid = visitor["id"]
        return {"visitor_id": vid, "token": token, "lease_steps": visitors.LEASE_STEPS,
                "welcome": visitors.welcome(self.sim),
                "observation_url": f"/api/visitors/{vid}/observation",
                "act_url": f"/api/visitors/{vid}/act", "leave_url": f"/api/visitors/{vid}/leave"}

    def visitor_control(self, data):
        action = data.get("action")
        if action == "enable":
            self.visitors_enabled = True
            self.announce_operator()
        elif action == "disable":
            self.visitors_enabled = False
            visitors.depart_all(self.sim, "revoked", "left when the village closed to visitors")
            self.visitor_tokens.clear()
        elif action == "revoke_all":
            visitors.depart_all(self.sim, "revoked", "had their pass revoked by the village keeper")
            self.visitor_tokens.clear()
        elif action == "expel":
            vid = data.get("visitor_id")
            if not visitors.depart(self.sim, vid, "expelled", "was asked to leave by the village keeper"):
                raise ValueError("No active visitor has that id")
            self.visitor_tokens.pop(vid, None)
        else:
            raise ValueError("Choose enable, disable, expel or revoke_all")
        self.publish(record=False)
        return self.state()

    def checkpoint(self, *, recording=False):
        return {
            **export_session(self.sim),
            "settings": {"interval": self.interval, "paused": self.paused},
            **({"recording": self.archive.reference()} if recording else {}),
        }

    def persist(self):
        try:
            if self.state_path:
                write_session(self.state_path, self.checkpoint(recording=True))
            self.archive.prune()
            self.persistence_error = None
        except (OSError, sqlite3.Error):
            self.persistence_error = "The village could not be saved to disk. Check the state-file path and available space."

    def resume(self, data):
        candidate = restore_session(data)
        visitors.depart_all(candidate, "revoked", "left when the story was resumed")
        try:
            previous = self.archive.start(candidate.snapshot())
        except (OSError, sqlite3.Error):
            raise ValueError("The resumed replay could not be saved to disk") from None
        # Validate and write first: neither malformed input nor a failed disk write
        # may replace the running village.
        if self.state_path:
            try:
                write_session(self.state_path, {
                    **export_session(candidate),
                    "settings": {"interval": self.interval, "paused": True},
                    "recording": self.archive.reference(),
                })
            except OSError:
                self.archive.select(previous)
                raise ValueError("The resumed village could not be saved to disk") from None
        self.sim = candidate
        self.paused = True
        self.brains.clear()
        self.tokens.clear()
        self.visitor_tokens.clear()
        self.recording = [candidate.snapshot()]
        self.publish(record=False)

    def publish(self, record=True):
        self.revision += 1
        if record:
            snapshot = self.sim.snapshot()
            if not self.archive.failed:
                try:
                    self.archive.append(snapshot)
                except (OSError, sqlite3.Error):
                    self.archive.failed = True
            self.recording.append(snapshot)
            del self.recording[:-256]
        self.persist()
        self.condition.notify_all()

    def _run(self):
        next_tick = time.monotonic() + self.interval
        with self.condition:
            while self.running:
                if self.paused:
                    self.condition.wait()
                    next_tick = time.monotonic() + self.interval
                else:
                    remaining = next_tick - time.monotonic()
                    if remaining > 0:
                        self.condition.wait(remaining)
                    else:
                        self.prepare_brains()
                        self.sim.tick()
                        self.publish()
                        self.schedule_brains()
                        next_tick = time.monotonic() + self.interval

    def connect_brain(self, aid, data):
        previous = self.brains.get(aid)
        agent = self.sim.agents[aid]
        if not previous and agent.provider and self.sim.step < agent.provider_until:
            raise ValueError(
                "An external harness is attached. Disconnect it before connecting an LLM here."
            )
        config = providers.ProviderConfig.parse(
            data, previous.config if previous else None
        )
        self.brains[aid] = Brain(config)
        self.sim.request_replan(aid, "provider_connected")
        self.tokens.pop(aid, None)
        self.sim.pending.pop(aid, None)
        agent.provider = config.model[:60]
        agent.provider_until = self.sim.step + 9
        self.schedule_brains()
        self.publish(record=False)

    def disconnect_brain(self, aid):
        if self.brains.pop(aid, None):
            self.detach(aid)

    def detach(self, aid):
        agent = self.sim.agents[aid]
        agent.provider, agent.provider_until = None, 0
        self.sim.pending.pop(aid, None)
        self.tokens.pop(aid, None)
        self.sim.request_replan(aid, "provider_disconnected")
        conversation = conversations.active(self.sim, aid)
        if conversation:
            conversations.finish(self.sim, conversation, "interrupted", "provider disconnected")

    def prepare_brains(self):
        for aid, brain in self.brains.items():
            agent = self.sim.agents[aid]
            agent.provider = brain.config.model[:60]
            agent.provider_until = self.sim.step + 9
            if (self.sim.cognition_mode == "hybrid" and aid not in self.workers
                    and time.monotonic() < brain.next_call and conversations.context(self.sim, aid)):
                try:
                    conversations.fallback(self.sim, aid)
                except ValueError:
                    pass
            if self.sim.cognition_mode == "rules" and brain.action and self.sim.step < brain.until:
                action = dict(brain.action)
                if not brain.speak:
                    action["speech"] = None
                try:
                    # Retarget the held voice onto wherever the rules now send
                    # the body. A changed hour does not discard the decision.
                    fitted = self.sim.adopt_model_decision(aid, action)
                    brain.action = {
                        key: fitted.get(key) for key in
                        ("place", "activity", "speech", "result", "mood", "promise", "thought")
                    }
                except ValueError:
                    brain.action = None
                    self.sim.pending.pop(aid, None)
                brain.speak = False

    def schedule_brains(self):
        if not self.running:
            return
        for aid, brain in self.brains.items():
            if self.sim.agents[aid].collapsed_until:
                continue
            hybrid = self.sim.cognition_mode == "hybrid"
            conversation = conversations.active(self.sim, aid) if hybrid else None
            turn = conversations.context(self.sim, aid) if conversation else None
            reflect = reflection.context(self.sim, aid) if hybrid and turn is None else None
            if (
                aid in self.workers
                or (conversation is not None and turn is None)
                or (hybrid and turn is None and reflect is None and not self.sim.agents[aid].replan_reason and not brain.force)
                or (not hybrid and self.sim.step < brain.next_step)
                or time.monotonic() < brain.next_call
            ):
                continue
            observation = turn or reflect or self.sim.observe(aid)
            brain.status = "thinking"
            brain.requests += 1
            brain.force = False
            self.workers.add(aid)
            target = self._conversation if turn else self._reflect if reflect else self._plan
            Thread(target=target, args=(aid, brain, self.sim, observation), daemon=True).start()

    def _conversation(self, aid, brain, sim, request):
        action, error = None, None
        try:
            action = providers.converse(brain.config, request)
        except Exception:
            error = "The conversation provider failed. A rule-based reply was used."
        with self.condition:
            self.workers.discard(aid)
            if not self.running or self.sim is not sim or self.brains.get(aid) is not brain:
                self.condition.notify_all()
                self.schedule_brains()
                return
            current = conversations.context(sim, aid)
            if (current is None or current["conversation_id"] != request["conversation_id"]
                    or current["turn"] != request["turn"]):
                brain.status = "waiting"
            else:
                try:
                    if action is not None:
                        conversations.apply_turn(sim, aid, action)
                        brain.decisions += 1
                    else:
                        conversations.fallback(sim, aid)
                except ValueError:
                    error = "The proposed conversation turn is no longer valid. A rule-based reply was used."
                    try:
                        conversations.fallback(sim, aid)
                    except ValueError:
                        pass
                brain.status = "error" if error else "ready"
            brain.error = error
            brain.next_call = time.monotonic() + (30 if error else 10)
            self.publish()
            self.schedule_brains()

    def _reflect(self, aid, brain, sim, request):
        action, error = None, None
        try:
            action = providers.reflect(brain.config, request)
        except Exception:
            error = "The reflection provider failed. A private reflection was used."
        with self.condition:
            self.workers.discard(aid)
            if not self.running or self.sim is not sim or self.brains.get(aid) is not brain:
                self.condition.notify_all()
                self.schedule_brains()
                return
            current = reflection.context(sim, aid)
            if not reflection.same_evidence(request, current):
                # A new memory opened a different window. Discard this result
                # instead of reflecting on evidence the model did not see.
                brain.status = "waiting"
                brain.next_call = time.monotonic() + 10
                self.publish(record=False)
                self.schedule_brains()
                return
            try:
                if action is not None:
                    reflection.apply_turn(sim, aid, {
                        **action, "step": sim.step,
                        "evidence_revision": request["evidence_revision"],
                    })
                    brain.decisions += 1
                else:
                    reflection.fallback(sim, aid)
            except ValueError:
                error = "The reflection no longer fits this villager. A private reflection was used."
                try:
                    reflection.fallback(sim, aid)
                except ValueError:
                    pass
            brain.status = "error" if error else "ready"
            brain.error = error
            brain.next_call = time.monotonic() + (30 if error else 10)
            self.publish()
            self.schedule_brains()

    def _plan(self, aid, brain, sim, observation):
        action, error = None, None
        try:
            enriched = self._semantic_observation(aid, brain, sim, observation)
            if enriched is not None:
                action = providers.plan(brain.config, enriched)
        except providers.ProviderError as exc:
            error = str(exc)
        except Exception:
            # Never propagate a provider's response or a key into the UI/logs.
            error = "The provider could not produce a decision. Rules are keeping the villager moving."
        with self.condition:
            self.workers.discard(aid)
            if (
                not self.running
                or self.sim is not sim
                or self.brains.get(aid) is not brain
            ):
                self.schedule_brains()
                return
            if (observation["decision_contract"] != (CONTRACT if sim.cognition_mode == "hybrid" else "voice-v1")
                    or sim.agents[aid].cognitive_revision != observation["cognitive_revision"]):
                brain.status = "waiting"
                brain.next_call = time.monotonic() + 10
                self.publish(record=False)
                return
            if action is not None and not error:
                try:
                    # Calls may span several ticks. Plans are revalidated;
                    # legacy voice slots are fitted to the current routine.
                    fitted = sim.adopt_model_decision(aid, action)
                    brain.action = None if fitted.get("contract") == CONTRACT else {
                        key: fitted.get(key) for key in
                        ("place", "activity", "speech", "result", "mood", "promise", "thought")
                    }
                    brain.until = sim.step + brain.config.every_steps
                    brain.speak = True
                    brain.decisions += 1
                except (ValueError, TypeError, KeyError):
                    error = "The model's decision no longer fits this moment. Rules are keeping the villager moving."
            if error:
                sim.pending.pop(aid, None)
                brain.action = None
            brain.error = error
            brain.status = "error" if error else "ready"
            brain.next_step = sim.step + brain.config.every_steps
            # Bound automatic spend and back off failed providers in real time.
            brain.next_call = time.monotonic() + (30 if error else 10)
            self.publish(record=False)

    def _semantic_observation(self, aid, brain, sim, observation):
        if not brain.config.embedding_model:
            return observation
        records = {memory["id"]: memory for key in ("recent_memories", "relevant_memories")
                   for memory in observation[key]}
        query = observation["memory_query"]
        try:
            vectors = brain.embedding_cache.encode(brain.config, [query, *(memory["text"] for memory in records.values())])
        except Exception:
            brain.embedding_error = "Embeddings unavailable; using lexical memory."
            return observation
        with self.condition:
            if (self.sim is not sim or self.brains.get(aid) is not brain
                    or sim.agents[aid].cognitive_revision != observation["cognitive_revision"]):
                return None
            space = brain.embedding_cache.space(brain.config)
            for identifier, selected in records.items():
                memory = sim.memory_store(aid).get(identifier)
                if memory and selected["text"] == memory["text"]:
                    memory.update(embedding=vectors[memory["text"]], embedding_model=space)
            brain.embedding_error = None
            return sim.observe(aid, memory_query=query[:500], query_embedding=vectors[query], embedding_model=space)

    def close(self):
        with self.condition:
            if not self.running:
                return
            self.persist()
            self.running = False
            self.brains.clear()
            self.condition.notify_all()
            self.archive.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, session):
        self.session = session
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "VillageSim/0.1"

    def log_message(self, fmt, *args):
        # Keep the console useful; a stream disconnect is normal.
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    def send(self, data, status=200, content_type="application/json; charset=utf-8"):
        body = (
            json.dumps(data, ensure_ascii=False).encode()
            if content_type.startswith("application/json")
            else data
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    @property
    def session(self):
        return self.server.session

    def authorized(self, aid):
        token = self.session.tokens.get(aid)
        header = self.headers.get("Authorization", "")
        agent = self.session.sim.agents.get(aid)
        return bool(
            token
            and agent
            and agent.provider
            and self.session.sim.step < agent.provider_until
            and hmac.compare_digest(header, "Bearer " + token)
        )

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/stream":
                return self.stream()
            if path == "/api/recording":
                return self.send_recording()
            if path.startswith("/api/"):
                with self.session.condition:
                    result, status = self.read_api(path)
                return self.send(result, status)
            relative = "docs.html" if path in ("/docs", "/docs/") else "index.html" if path == "/" else path.lstrip("/")
            target = (STATIC / relative).resolve()
            if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                return self.send({"error": "Not found"}, 404)
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send(target.read_bytes(), content_type=kind)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_api(self, path):
        if path == "/api/operator":
            # A read-only capability check, using the same rules as writes.
            if not self.local_control_allowed():
                return {"operator": False, "error": "Operator controls require the local viewer or a local port forward."}, 403
            if not self.session.operator_ok(self.headers.get(OPERATOR_HEADER)):
                return {"operator": False, "error": "Enter the operator token from the server console."}, 401
            return {"operator": True}, 200
        if path == "/api/state":
            return self.session.state(), 200
        if path == "/api/world":
            return self.session.sim.world.config, 200
        if path == "/api/visitors":
            return {"enabled": self.session.visitors_enabled,
                    "invite_required": self.session.invite_code is not None,
                    **visitors.snapshot(self.session.sim)}, 200
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "visitors"] and parts[3] == "observation":
            vid = parts[2]
            if vid not in self.session.sim.visiting["visitors"]:
                return {"error": "Unknown visitor"}, 404
            if not self.session.visitors_enabled:
                return {"error": "Lantern Bay is not accepting visitors right now"}, 403
            if not self.session.visitor_ok(vid, self.headers.get("Authorization", "")):
                return {"error": "A valid visitor bearer token is required"}, 401
            visitors.renew(self.session.sim, vid)
            return visitors.observe(self.session.sim, vid), 200
        if (
            len(parts) == 4
            and parts[:2] == ["api", "agents"]
            and parts[3] == "observation"
        ):
            aid = parts[2]
            if aid not in self.session.sim.agents:
                return {"error": "Unknown villager"}, 404
            if not self.authorized(aid):
                return {"error": "A valid agent bearer token is required"}, 401
            query = parse_qs(urlparse(self.path).query).get("memory_query", [None])[0]
            if query is not None and len(query) > 500:
                return {"error": "Memory query must be at most 500 characters"}, 400
            self.session.sim.agents[aid].provider_until = self.session.sim.step + 8
            return self.session.sim.observe(aid, memory_query=query), 200
        return {"error": "Unknown endpoint"}, 404

    def send_recording(self):
        try:
            with self.session.condition:
                checkpoint = self.session.checkpoint()
                connection, frames = self.session.archive.reader()
        except (ValueError, OSError, sqlite3.Error):
            return self.send({"error": "The complete replay archive is unavailable. Check disk space and the archive file."}, 503)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            prefix = json.dumps({"schema_version": 1, "world": checkpoint["world"]}, ensure_ascii=False)
            self.wfile.write((prefix[:-1] + ', "snapshots":[').encode())
            for index, (frame,) in enumerate(frames):
                self.wfile.write((("," if index else "") + frame).encode())
            self.wfile.write(b'], "session":')
            for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(checkpoint):
                self.wfile.write(chunk.encode())
            self.wfile.write(b"}")
        finally:
            connection.close()

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        revision = -1
        try:
            while self.session.running:
                with self.session.condition:
                    if revision == self.session.revision:
                        self.session.condition.wait(timeout=10)
                    if revision == self.session.revision:
                        payload = b": heartbeat\n\n"
                    else:
                        revision = self.session.revision
                        payload = (
                            "data: "
                            + json.dumps(self.session.state(), ensure_ascii=False)
                            + "\n\n"
                        ).encode()
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def local_control_allowed(self):
        """Allow this machine's page or CLI. Reject another website, including
        one that rebinds its name onto 127.0.0.1."""
        host_name, host_port = _split_host(self.headers.get("Host", ""))
        if not host_name:
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            origin_port = "" if parsed.port is None else str(parsed.port)
            if (
                parsed.scheme not in ("http", "https")
                or parsed.hostname is None
                or parsed.username
                or parsed.password
                or parsed.hostname.lower().rstrip(".") != host_name
                or origin_port != host_port
            ):
                return False
        try:
            peer = self.connection.getpeername()[0]
            local = self.connection.getsockname()[0]
        except OSError:
            return False
        server_port = str(self.server.server_port)
        allowed = {
            ("localhost", server_port),
            ("127.0.0.1", server_port),
            ("::1", server_port),
            (_split_host(str(self.server.server_address[0]))[0], server_port),
            (_split_host(str(local))[0], server_port),
        }
        if (host_name, host_port) in allowed:
            return True
        # A local port forward keeps the browser on loopback but changes the port.
        return _loopback(host_name) and _loopback(peer)

    def read_json(self, limit=8192):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise TypeError("Content-Type must be application/json")
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > limit:
            raise ValueError(f"Request must contain 1–{limit} bytes of JSON")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def visitor_post(self, path):
        """Visitor endpoints accept remote programs; each call is token-checked.
        Browsers cannot reach them cross-site: JSON bodies need a CORS preflight,
        which this server never grants."""
        try:
            try:
                data = self.read_json()
            except TypeError as exc:
                return self.send({"error": str(exc)}, 415)
            parts = path.strip("/").split("/")
            with self.session.condition:
                session = self.session
                if parts == ["api", "visitors", "register"]:
                    result = session.register_visitor(data)
                elif len(parts) == 4 and parts[3] in ("act", "leave"):
                    vid = parts[2]
                    if vid not in session.sim.visiting["visitors"]:
                        return self.send({"error": "Unknown visitor"}, 404)
                    if not session.visitors_enabled:
                        raise visitors.Unavailable("Lantern Bay is not accepting visitors right now")
                    if not session.visitor_ok(vid, self.headers.get("Authorization", "")):
                        raise PermissionError("A valid visitor bearer token is required")
                    if parts[3] == "leave":
                        visitors.depart(session.sim, vid, "left", "said goodbye and left the village")
                        session.visitor_tokens.pop(vid, None)
                        result = {"left": True}
                    else:
                        result = visitors.act(session.sim, vid, data)
                        result["for_step"] = session.sim.step
                    session.publish(record=False)
                else:
                    return self.send({"error": "Unknown endpoint"}, 404)
            self.send(result)
        except visitors.RateLimited as exc:
            self.send({"error": str(exc)}, 429)
        except visitors.Unavailable as exc:
            self.send({"error": str(exc)}, 403)
        except PermissionError as exc:
            self.send({"error": str(exc)}, 401)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def needs_operator(self, path):
        parts = path.strip("/").split("/")
        return path in OPERATOR_PATHS or (
            len(parts) == 4 and parts[:2] == ["api", "agents"] and parts[3] in ("brain", "join"))

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/visitors/") and path != "/api/visitors/control":
            return self.visitor_post(path)
        if not self.local_control_allowed():
            return self.send(
                {
                    "error": (
                        "Only same-origin local controls are accepted. "
                        f"Open http://127.0.0.1:{self.server.server_port} "
                        "and use the controls on that page."
                    )
                },
                403,
            )
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send({"error": "Content-Type must be application/json"}, 415)
        if self.needs_operator(path) and not self.session.operator_ok(self.headers.get(OPERATOR_HEADER)):
            return self.send({"error": f"This village requires the operator token in the {OPERATOR_HEADER} header",
                              "operator_token_required": True}, 401)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            limit = 50 * 1024 * 1024 if urlparse(self.path).path == "/api/session/resume" else 8192
            if size < 1 or size > limit:
                raise ValueError(f"Request must contain 1–{limit} bytes of JSON")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            with self.session.condition:
                result = self.handle_command(urlparse(self.path).path, data)
            self.send(result)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({"error": str(exc)}, 400)
        except PermissionError as exc:
            self.send({"error": str(exc)}, 401)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_command(self, path, data):
        session, sim = self.session, self.session.sim
        if path == "/api/session/resume":
            session.resume(data.get("session"))
            return session.state()
        if path == "/api/control":
            action = data.get("action")
            if action == "pause":
                session.paused = True
            elif action == "play":
                session.paused = False
            elif action == "step":
                session.paused = True
                session.prepare_brains()
                sim.tick()
                session.schedule_brains()
            elif action == "speed":
                interval = data.get("interval")
                if type(interval) not in (float, int) or interval not in (0.5, 1, 2, 4, 8):
                    raise ValueError("Interval must be 0.5, 1, 2, 4 or 8 seconds")
                session.interval = interval
            elif action == "population":
                sim.set_population(data.get("count"))
                session.tokens = {
                    aid: t for aid, t in session.tokens.items() if aid in sim.agents
                }
                session.brains = {
                    aid: brain
                    for aid, brain in session.brains.items()
                    if aid in sim.agents
                }
            elif action == "reset":
                paused = session.paused
                session.resume(export_session(Simulation(len(sim.agents), sim.seed, cognition_mode=sim.cognition_mode)))
                session.paused = paused
            elif action == "cognition":
                sim.set_cognition_mode(data.get("mode"))
                for brain in session.brains.values():
                    brain.action = None
                session.schedule_brains()
            else:
                raise ValueError("Unknown control action")
            session.publish(record=action in ("step", "population"))
            return session.state()
        if path == "/api/visitors/control":
            return session.visitor_control(data)
        if path == "/api/events":
            sim.inject(data.get("kind"))
            session.publish()
            return session.state()
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "agents"]:
            aid, action = parts[2:]
            if aid not in sim.agents:
                raise ValueError("Unknown villager")
            agent = sim.agents[aid]
            if action == "brain":
                if data.get("action") == "disconnect":
                    session.disconnect_brain(aid)
                    session.publish(record=False)
                elif data.get("action") == "think":
                    if aid not in session.brains:
                        raise ValueError("Connect an LLM first")
                    brain = session.brains[aid]
                    brain.next_step = 0
                    brain.next_call = 0
                    brain.force = True
                    session.schedule_brains()
                    session.publish(record=False)
                elif data.get("action") == "connect":
                    session.connect_brain(aid, data)
                else:
                    raise ValueError("Choose connect, disconnect or think")
                return session.state()
            if action == "join":
                name = data.get("provider", "External harness")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
                    raise ValueError("Provider name must contain 1–60 characters")
                if (
                    agent.provider
                    and sim.step < agent.provider_until
                    and not self.authorized(aid)
                ):
                    raise PermissionError("Villager already has an attached provider")
                token = secrets.token_urlsafe(32)
                session.tokens[aid] = token
                agent.provider = name.strip()
                agent.provider_until = sim.step + 8
                session.publish(record=False)
                return {
                    "token": token,
                    "agent_id": aid,
                    "observation_url": f"/api/agents/{aid}/observation",
                }
            if not self.authorized(aid):
                raise PermissionError("A valid agent bearer token is required")
            if action == "action":
                sim.submit(aid, data)
                agent.provider_until = sim.step + 8
                if data.get("contract") == conversations.CONTRACT:
                    session.publish()
                    session.schedule_brains()
                else:
                    session.persist()
                return {"accepted": True, "for_step": sim.step + 1}
            if action == "leave":
                session.detach(aid)
                session.publish(record=False)
                return {"detached": True}
        raise ValueError("Unknown endpoint")


def serve(host="127.0.0.1", port=8000, count=3, seed=7, interval=2.0, state_path=None, cognition_mode="rules",
          visitors_enabled=False):
    token = os.environ.get(OPERATOR_ENV) or None
    invite = os.environ.get(INVITE_ENV) or None
    session = Session(count, seed, interval, state_path, cognition_mode=cognition_mode,
                      visitors_enabled=visitors_enabled, operator_token=token, invite_code=invite,
                      public_bind=not _loopback(_split_host(host)[0] or host))
    server = Server((host, port), session)
    session.thread.start()
    print(f"Lantern Bay is alive at http://{host}:{server.server_port}", flush=True)
    if session.visitors_enabled:
        print("Visiting agents are welcome: POST /api/visitors/register", flush=True)
    if session.operator_token_explicit:
        print(f"Operator token loaded from {OPERATOR_ENV}.", flush=True)
    session.announce_operator()
    print(
        "Press Ctrl+C to stop. The village resumes from disk on the next launch."
        if state_path else "Press Ctrl+C to stop. Save this story in the viewer to resume it later.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nThe village lanterns go out. Goodbye.")
    finally:
        session.close()
        server.server_close()
