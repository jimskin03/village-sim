"""One local process serving static UI, SSE snapshots and optional agent actions."""

from __future__ import annotations

import hmac
import json
import mimetypes
from pathlib import Path
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Thread
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse, urlsplit

from . import providers
from .cognition.planning import CONTRACT
from .cognition.embeddings import EmbeddingCache
from .simulation import Simulation
from .persistence import export_session, read_session, restore_session, write_session

STATIC = Path(__file__).parent / "static"


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
    def __init__(self, count=3, seed=7, interval=2.0, state_path=None, cognition_mode="rules"):
        self.condition = Condition()
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
        self.persistence_error = None
        self.running = True
        self.revision = 0
        self.tokens: dict[str, str] = {}
        self.brains: dict[str, Brain] = {}
        self.workers: set[str] = set()
        self.recording = [self.sim.snapshot()]
        self.thread = Thread(target=self._run, daemon=True)
        self.persist()

    def state(self):
        return {
            **self.sim.snapshot(),
            "paused": self.paused,
            "interval": self.interval,
            "revision": self.revision,
            "brains": {aid: brain.public() for aid, brain in self.brains.items()},
            "persistence_error": self.persistence_error,
        }

    def checkpoint(self):
        return {
            **export_session(self.sim),
            "settings": {"interval": self.interval, "paused": self.paused},
        }

    def persist(self):
        if self.state_path:
            try:
                write_session(self.state_path, self.checkpoint())
                self.persistence_error = None
            except OSError:
                self.persistence_error = "The village could not be saved to disk. Check the state-file path and available space."

    def resume(self, data):
        candidate = restore_session(data)
        # Validate and write first: neither malformed input nor a failed disk write
        # may replace the running village.
        if self.state_path:
            try:
                write_session(self.state_path, {
                    **export_session(candidate),
                    "settings": {"interval": self.interval, "paused": True},
                })
            except OSError:
                raise ValueError("The resumed village could not be saved to disk") from None
        self.sim = candidate
        self.paused = True
        self.brains.clear()
        self.tokens.clear()
        self.recording = [candidate.snapshot()]
        self.publish(record=False)

    def publish(self, record=True):
        self.persist()
        self.revision += 1
        if record:
            self.recording.append(self.sim.snapshot())
            del self.recording[:-256]
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
            self.sim.agents[aid].provider = None
            self.sim.pending.pop(aid, None)

    def prepare_brains(self):
        for aid, brain in self.brains.items():
            agent = self.sim.agents[aid]
            agent.provider = brain.config.model[:60]
            agent.provider_until = self.sim.step + 9
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
            hybrid = self.sim.cognition_mode == "hybrid"
            if (
                aid in self.workers
                or (hybrid and not self.sim.agents[aid].replan_reason and not brain.force)
                or (not hybrid and self.sim.step < brain.next_step)
                or time.monotonic() < brain.next_call
            ):
                continue
            observation = self.sim.observe(aid)
            brain.status = "thinking"
            brain.requests += 1
            brain.force = False
            self.workers.add(aid)
            Thread(
                target=self._plan, args=(aid, brain, self.sim, observation), daemon=True
            ).start()

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
            self.persist()
            self.running = False
            self.brains.clear()
            self.condition.notify_all()


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
        if path == "/api/state":
            return self.session.state(), 200
        if path == "/api/world":
            return self.session.sim.world.config, 200
        if path == "/api/recording":
            # Recorded frames are immutable; copy the index under the lock,
            # then serialize and send without holding up the village clock.
            return {
                "schema_version": 1,
                "world": self.session.sim.world.config,
                "snapshots": list(self.session.recording),
                "session": self.session.checkpoint(),
            }, 200
        parts = path.strip("/").split("/")
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

    def do_POST(self):
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
                if type(interval) not in (float, int) or interval not in (0.5, 1, 2, 4):
                    raise ValueError("Interval must be 0.5, 1, 2 or 4 seconds")
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
                session.brains.clear()
                session.sim = Simulation(len(sim.agents), sim.seed, cognition_mode=sim.cognition_mode)
                session.tokens.clear()
                session.recording = [session.sim.snapshot()]
            elif action == "cognition":
                sim.set_cognition_mode(data.get("mode"))
                for brain in session.brains.values():
                    brain.action = None
                session.schedule_brains()
            else:
                raise ValueError("Unknown control action")
            session.publish(record=action in ("step", "population"))
            return session.state()
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
                session.persist()
                return {"accepted": True, "for_step": sim.step + 1}
            if action == "leave":
                agent.provider = None
                sim.pending.pop(aid, None)
                session.tokens.pop(aid, None)
                session.publish(record=False)
                return {"detached": True}
        raise ValueError("Unknown endpoint")


def serve(host="127.0.0.1", port=8000, count=3, seed=7, interval=2.0, state_path=None, cognition_mode="rules"):
    session = Session(count, seed, interval, state_path, cognition_mode=cognition_mode)
    server = Server((host, port), session)
    session.thread.start()
    print(f"Lantern Bay is alive at http://{host}:{server.server_port}", flush=True)
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
