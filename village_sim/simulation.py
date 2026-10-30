"""Pure simulation: perceive → retrieve → plan → reflect → execute.

All model state belongs here. External providers submit optional decisions; the
clock and default decisions never wait for them. Snapshots contain only JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import random
from typing import Callable

from .decisions import Action, daily_plan, decide
from .world import World

PERSONAS = [
    (
        "mara",
        "Mara",
        "fisher",
        "mara_home",
        "#f5b86b",
        "A patient fisher who knows every tide. She collects little stories as carefully as she mends her nets.",
    ),
    (
        "tom",
        "Tom",
        "boatwright",
        "tom_home",
        "#91c7cb",
        "A cheerful boatwright, always fixing something. He insists the best ideas arrive beside the fire.",
    ),
    (
        "lin",
        "Lin",
        "gardener",
        "lin_home",
        "#baa3e5",
        "A thoughtful gardener with an eye for small changes. She grows herbs for the whole village.",
    ),
    (
        "ada",
        "Ada",
        "fisher",
        "guest_home",
        "#e7979e",
        "An adventurous fisher who reads the clouds and loves a good evening story.",
    ),
    (
        "ivo",
        "Ivo",
        "gardener",
        "guest_home",
        "#a7c78c",
        "A quiet herbalist who finds something worth remembering on every walk.",
    ),
    (
        "nell",
        "Nell",
        "boatwright",
        "tom_home",
        "#d8a5cf",
        "A curious craftsperson who listens closely and repairs the village's wooden boats.",
    ),
    (
        "ren",
        "Ren",
        "fisher",
        "mara_home",
        "#d3cc87",
        "A young fisher learning the bay's rhythms and making friends along the shore.",
    ),
    (
        "bea",
        "Bea",
        "gardener",
        "lin_home",
        "#87b7e2",
        "A generous cook and gardener who brings everyone together over herbs and stories.",
    ),
]


@dataclass
class Agent:
    id: str
    name: str
    role: str
    home: str
    color: str
    persona: str
    x: int
    y: int
    activity: str = "settling into the evening"
    destination: str = "fire"
    speech: str | None = None
    memory: list[dict] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    relationships: dict[str, int] = field(default_factory=dict)
    provider: str | None = None
    provider_until: int = 0
    last_chat: int = -8
    last_work: int = -4
    work_count: int = 0


class Simulation:
    def __init__(self, count: int = 3, seed: int = 7, decision: Callable = decide):
        self.world = World()
        self.seed = seed
        self.rng = random.Random(seed)
        self.decision = decision
        self.step = 0
        self.weather = "clear"
        self.weather_until = 0
        self.festival_until = 0
        self.agents: dict[str, Agent] = {}
        self.residents: dict[str, Agent] = {}
        self.pending: dict[str, Action] = {}
        self.events: list[dict] = []
        self.set_population(count)
        self.event(
            "The lanterns are lit. A new evening begins in Lantern Bay.", "village"
        )

    @property
    def total_minutes(self):
        return (
            self.world.config["start_minute"]
            + self.step * self.world.config["minutes_per_step"]
        )

    @property
    def minute(self):
        return self.total_minutes % 1440

    @property
    def day(self):
        return self.total_minutes // 1440 + 1

    def event(self, text: str, kind: str, agent_ids=()):
        item = {
            "id": len(self.events),
            "step": self.step,
            "day": self.day,
            "time": f"{self.minute // 60:02}:{self.minute % 60:02}",
            "text": text,
            "kind": kind,
            "agents": list(agent_ids),
        }
        self.events.append(item)
        for aid in agent_ids:
            self.agents[aid].memory.append(dict(item))

    def set_population(self, count: int):
        if type(count) is not int or not 1 <= count <= len(PERSONAS):
            raise ValueError("Population must be an integer between 1 and 8")
        wanted = {p[0] for p in PERSONAS[:count]}
        for aid in list(self.agents):
            if aid not in wanted:
                self.event(
                    f"{self.agents[aid].name} left the village for a while.",
                    "arrival",
                    [aid],
                )
                self.agents[aid].provider = None
                self.pending.pop(aid, None)
                del self.agents[aid]
        for persona in PERSONAS[:count]:
            aid = persona[0]
            if aid not in self.agents:
                if aid not in self.residents:
                    x, y = self.world.point(persona[3])
                    self.residents[aid] = Agent(*persona, x=x, y=y)
                    self.residents[aid].plan = daily_plan(self.residents[aid])
                self.agents[aid] = self.residents[aid]
                self.event(
                    f"{self.agents[aid].name} joined the village.", "arrival", [aid]
                )

    def observe(self, aid: str) -> dict:
        agent = self.agents[aid]
        nearby = [
            other
            for other in self.agents.values()
            if other.id != aid and abs(other.x - agent.x) + abs(other.y - agent.y) <= 4
        ]
        # Retrieval is a view, never deletion: all session memories remain stored.
        relevant = sorted(
            agent.memory,
            key=lambda m: (m["kind"] in ("conversation", "reflection"), m["step"]),
            reverse=True,
        )[:12]
        return {
            "step": self.step,
            "day": self.day,
            "minute": self.minute,
            "weather": self.weather,
            "festival": self.step < self.festival_until,
            "agent": asdict(agent),
            "nearby": [
                {"id": a.id, "name": a.name, "activity": a.activity} for a in nearby
            ],
            "relevant_memories": relevant,
            "places": self.world.config["places"],
            "suggested_action": asdict(
                decide(
                    agent,
                    {
                        "minute": self.minute,
                        "weather": self.weather,
                        "festival": self.step < self.festival_until,
                    },
                )
            ),
        }

    def submit(self, aid: str, data: dict):
        if aid not in self.agents:
            raise ValueError("Unknown villager")
        if type(data.get("step")) is not int or data["step"] != self.step:
            raise ValueError("Decision is stale; fetch a fresh observation")
        place, activity, speech = (
            data.get("place"),
            data.get("activity"),
            data.get("speech"),
        )
        if not isinstance(place, str) or place not in self.world.places:
            raise ValueError("Choose a named place from the observation")
        if not isinstance(activity, str) or not 1 <= len(activity.strip()) <= 100:
            raise ValueError("Activity must contain 1–100 characters")
        if speech is not None and (not isinstance(speech, str) or len(speech) > 200):
            raise ValueError("Speech must be text, at most 200 characters")
        self.pending[aid] = Action(place, activity.strip(), speech)

    def inject(self, kind: str):
        if kind == "storm":
            self.weather = "storm"
            self.weather_until = self.step + 12
            self.event(
                "Rain rolls across the bay. The villagers head for shelter.",
                "weather",
                self.agents,
            )
        elif kind == "festival":
            self.festival_until = self.step + 16
            self.event(
                "Paper lanterns gather at the hearth. The evening festival begins!",
                "festival",
                self.agents,
            )
        elif kind == "clear":
            self.weather = "clear"
            self.weather_until = self.festival_until = 0
            self.event(
                "The bay is quiet again. Everyone returns to their own rhythm.",
                "weather",
                self.agents,
            )
        else:
            raise ValueError("Unknown event; choose storm, festival, or clear")

    def tick(self):
        previous_day = self.day
        self.step += 1
        if self.weather == "storm" and self.step >= self.weather_until:
            self.weather = "clear"
            self.event(
                "The rain passes. The water glitters beneath the sky.",
                "weather",
                self.agents,
            )
        if self.festival_until and self.step == self.festival_until:
            self.event(
                "The last festival lantern drifts away. Daily life resumes.",
                "festival",
                self.agents,
            )
        for agent in self.agents.values():
            agent.speech = None
            if self.day != previous_day:
                agent.plan = daily_plan(agent)
                self.event(
                    f"{agent.name} reflects on {len(agent.memory)} memories and looks forward to another day.",
                    "reflection",
                    [agent.id],
                )
            observation = self.observe(agent.id)
            action = self.pending.pop(agent.id, None)
            # World events always take precedence over an optional external plan.
            if (
                action is None
                or self.weather == "storm"
                or self.step < self.festival_until
            ):
                action = self.decision(agent, observation)
            if agent.provider and self.step >= agent.provider_until:
                agent.provider = None
            agent.destination = action.place
            path = self.world.path((agent.x, agent.y), self.world.point(action.place))
            if path:
                agent.x, agent.y = path[min(2, len(path) - 1)]
            arrived = (agent.x, agent.y) == self.world.point(action.place)
            activity = (
                action.activity
                if arrived
                else f"walking to {self.world.places[action.place]['name']}"
            )
            if activity != agent.activity:
                agent.activity = activity
                if arrived:
                    self.event(f"{agent.name} is {activity}.", "activity", [agent.id])
            if action.speech:
                agent.speech = action.speech
                self.event(
                    f"{agent.name}: “{action.speech}”", "conversation", [agent.id]
                )
            if (
                arrived
                and self.step - agent.last_work >= 4
                and action.place in ("jetty", "shore", "garden", "nets")
            ):
                agent.last_work = self.step
                agent.work_count += 1
                detail = {
                    "jetty": "caught a silver fish",
                    "shore": "checked the boats along the shore",
                    "garden": "gathered fresh herbs for the village",
                    "nets": "repaired a worn patch of net",
                }[action.place]
                self.event(f"{agent.name} {detail}.", "work", [agent.id])
        self._converse()
        return self.snapshot()

    def _converse(self):
        lines = [
            "The tide has a story to tell tonight.",
            "Come sit a while. There’s room by the fire.",
            "I saved some herbs for tomorrow’s supper.",
            "A quiet moment is a good thing to share.",
            "Did you see the light out on the water?",
            "Let’s look after this little place together.",
        ]
        villagers = list(self.agents.values())
        for i, first in enumerate(villagers):
            for other in villagers[i + 1 :]:
                if min(self.step - first.last_chat, self.step - other.last_chat) < 8:
                    continue
                if "sleeping" in (first.activity, other.activity):
                    continue
                if abs(first.x - other.x) + abs(first.y - other.y) <= 2:
                    line = self.rng.choice(lines)
                    first.speech = line
                    first.last_chat = other.last_chat = self.step
                    first.relationships[other.id] = (
                        first.relationships.get(other.id, 0) + 1
                    )
                    other.relationships[first.id] = (
                        other.relationships.get(first.id, 0) + 1
                    )
                    self.event(
                        f"{first.name} to {other.name}: “{line}”",
                        "conversation",
                        [first.id, other.id],
                    )

    def snapshot(self):
        return {
            "schema_version": 1,
            "village": self.world.config["name"],
            "seed": self.seed,
            "step": self.step,
            "day": self.day,
            "minute": self.minute,
            "time": f"{self.minute // 60:02}:{self.minute % 60:02}",
            "weather": self.weather,
            "festival": self.step < self.festival_until,
            "agents": [asdict(a) for a in self.agents.values()],
            "events": self.events[-80:],
            "event_count": len(self.events),
        }
