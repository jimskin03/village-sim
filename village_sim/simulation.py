"""Pure simulation: perceive → retrieve → plan → reflect → execute.

All model state belongs here. External providers submit optional decisions; the
clock and default decisions never wait for them. Snapshots contain only JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
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
    relationships: dict[str, str] = field(default_factory=dict)
    goals: list[dict] = field(default_factory=list)
    provider: str | None = None
    provider_until: int = 0
    last_chat: int = -8
    last_work: int = -4
    work_count: int = 0
    thought: str | None = None
    mood: str = "content"
    result: str | None = None


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
        self.next_event_id = 0
        self.seen_texts: dict[str, bool] = {}
        self.pantry = {"fish": 0, "herbs": 0, "boat_condition": "sound",
                       "net_condition": "sound", "hearth_lit": True}
        self.suppers: list[dict] = []
        self.reflections: list[dict] = []
        self.visitor: dict | None = None
        self.daily_work: dict[str, dict] = {}
        self.last_miss: dict[str, str] = {}
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
        normalized = " ".join(text.casefold().split())
        if normalized in self.seen_texts:
            return None
        self.seen_texts[normalized] = True
        item = {
            "id": self.next_event_id,
            "step": self.step,
            "day": self.day,
            "time": f"{self.minute // 60:02}:{self.minute % 60:02}",
            "text": text,
            "kind": kind,
            "agents": list(agent_ids),
        }
        self.next_event_id += 1
        self.events.append(item)
        for aid in agent_ids:
            self.agents[aid].memory.append(dict(item))
        return item

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
        for agent in self.agents.values():
            self._refresh_goals(agent)

    def _social_goal(self, agent: Agent) -> dict | None:
        others = [other for other in self.agents.values() if other.id != agent.id]
        if not others:
            return None
        # Rotate equal acquaintances across days; established friendships win.
        offset = (self.day + list(self.agents).index(agent.id)) % len(others)
        others = others[offset:] + others[:offset]
        partner = next((other for other in others if other.id in agent.relationships), others[0])
        return {
            "id": "social",
            "day": self.day,
            "title": f"Share a conversation with {partner.name}",
            "target": 1,
            "progress": 0,
            "completed": False,
            "place": "fire" if partner.id in agent.relationships else "bench",
            "partner": partner.id,
        }

    def _refresh_goals(self, agent: Agent):
        if not agent.goals or agent.goals[0]["day"] != self.day:
            place, title = {
                "fisher": ("jetty", "Catch 3 fish for the village supper"),
                "gardener": ("garden", "Gather 3 herb bundles for the village supper"),
                "boatwright": ("shore", "Check 3 boats for the village"),
            }[agent.role]
            if agent.role in self.last_miss:
                title = self.last_miss[agent.role]
                if agent.role == "fisher":
                    place = "shore"
            agent.goals = [
                {
                    "id": "work",
                    "day": self.day,
                    "title": title,
                    "target": 3,
                    "progress": 0,
                    "completed": False,
                    "place": place,
                    "partner": None,
                }
            ]
        social = next((g for g in agent.goals if g["id"] == "social"), None)
        # A completed goal remains a record of the day even when its partner leaves.
        if social and (social["completed"] or social["partner"] in self.agents):
            return
        if social:
            agent.goals.remove(social)
        replacement = self._social_goal(agent)
        if replacement:
            agent.goals.append(replacement)

    def _progress_goal(self, agent: Agent, kind: str, *, place=None, partner=None):
        goal = next((g for g in agent.goals if g["id"] == kind), None)
        if not goal or goal["completed"]:
            return
        if kind == "work" and goal["place"] != place:
            return
        if kind == "social" and goal["partner"] != partner:
            return
        goal["progress"] = min(goal["target"], goal["progress"] + 1)
        if goal["progress"] == goal["target"]:
            goal["completed"] = True
            self.event(
                f"{agent.name} completed a daily goal: {goal['title']}.",
                "goal",
                [agent.id],
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
            key=lambda m: (
                m["kind"] in ("conversation", "reflection", "goal"), m["step"]
            ),
            reverse=True,
        )[:12]
        observation = {
            "step": self.step,
            "day": self.day,
            "minute": self.minute,
            "weather": self.weather,
            "festival": self.step < self.festival_until,
            "pantry": dict(self.pantry),
            "last_supper": copy.deepcopy(self.suppers[-1]) if self.suppers else None,
            "agent": asdict(agent),
            "nearby": [
                {"id": a.id, "name": a.name, "activity": a.activity} for a in nearby
            ],
            "villagers": [
                {
                    "id": a.id, "name": a.name, "activity": a.activity,
                    "destination": a.destination,
                }
                for a in self.agents.values()
                if a.id != aid
            ],
            "relevant_memories": relevant,
            "places": self.world.config["places"],
        }
        suggested = decide(agent, observation)
        observation["suggested_action"] = asdict(suggested)
        observation["allowed_actions"] = [{"place": suggested.place, "activity": suggested.activity}]
        observation["result_options"] = self._result_options(agent, suggested.place)
        observation["mood_options"] = ["content", "hopeful", "worried", "tired"]
        observation["promise_options"] = self._promise_options(agent)
        observation["speech_options"] = self._speech_options(agent)
        return observation

    def submit(self, aid: str, data: dict):
        if aid not in self.agents:
            raise ValueError("Unknown villager")
        if type(data.get("step")) is not int or data["step"] != self.step:
            raise ValueError("Decision is stale; fetch a fresh observation")
        observation = self.observe(aid)
        place = data.get("place", observation["suggested_action"]["place"])
        activity = data.get("activity", observation["suggested_action"]["activity"])
        if {"place": place, "activity": activity} not in observation["allowed_actions"]:
            raise ValueError("Choose a current allowed action; the rules set the activity")
        for field in ("result", "mood", "promise", "speech"):
            value = data.get(field)
            if value is not None and value not in observation[field + "_options"]:
                raise ValueError(f"Choose {field} from the observation's {field}_options")
        thought = data.get("thought")
        if thought is not None and (not isinstance(thought, str) or len(thought) > 200):
            raise ValueError("Thought must be text, at most 200 characters")
        self.pending[aid] = Action(place, activity, **{
            field: data.get(field) for field in ("speech", "result", "mood", "promise", "thought")
        })

    def inject(self, kind: str):
        if kind == "storm":
            self.weather = "storm"
            self.weather_until = self.step + 12
            self.pantry["net_condition"] = "torn"
            self.event(
                "Rain tears the village net and closes the jetty. The villagers head for shelter.",
                "weather",
                self.agents,
            )
        elif kind == "festival":
            if self.step < self.festival_until:
                return
            self.festival_until = self.step + 16
            fish, herbs = min(2, self.pantry["fish"]), min(1, self.pantry["herbs"])
            self.pantry["fish"] -= fish
            self.pantry["herbs"] -= herbs
            x, y = self.world.point("guest_home")
            self.visitor = {"id": "visitor", "name": "Wren", "place": "guest_home", "x": x, "y": y}
            text = f"Wren arrived at the guest cottage for the festival; everyone shared {fish} fish and {herbs} herb bundles."
            self.event(text, "festival", self.agents)
            for first in self.agents.values():
                for other in self.agents.values():
                    if first.id != other.id:
                        first.relationships[other.id] = f"{first.name} and {other.name} shared Wren’s lantern festival."
        elif kind == "clear":
            self.weather = "clear"
            self.weather_until = self.festival_until = 0
            self.visitor = None
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
        self.pantry["hearth_lit"] = self.minute >= 18 * 60 or self.minute < 6 * 60
        if self.festival_until and self.step >= self.festival_until:
            self.visitor = None
        if self.day != previous_day:
            self._reflect(previous_day)
        if self.minute >= 18 * 60 and not any(s["day"] == self.day for s in self.suppers):
            self._supper()
        for agent in self.agents.values():
            agent.speech = None
            agent.thought = None
            agent.result = None
            if self.day != previous_day:
                agent.plan = daily_plan(agent)
                self._refresh_goals(agent)
        for agent in self.agents.values():
            observation = self.observe(agent.id)
            action = self.pending.pop(agent.id, None)
            # World events always take precedence over an optional external plan.
            if (
                action is None
                or self.weather == "storm"
                or self.step < self.festival_until
            ):
                action = self.decision(agent, observation)
            # A delayed response cannot override a changed hour or world event.
            if action.place != observation["suggested_action"]["place"] or action.activity != observation["suggested_action"]["activity"]:
                action = self.decision(agent, observation)
            if action.mood in observation["mood_options"]:
                agent.mood = action.mood
            agent.thought = action.thought
            if action.promise in observation["promise_options"]:
                self._keep_promise(agent, action.promise)
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
            if action.speech and action.speech in observation["speech_options"]:
                agent.speech = action.speech
                self.event(
                    f"{agent.name}: “{action.speech}”", "conversation", [agent.id]
                )
            if arrived and self.step - agent.last_work >= 4 and self._result_options(agent, action.place):
                agent.last_work = self.step
                self._work(agent, action.place, action.result)
        self._converse()
        return self.snapshot()

    def _result_options(self, agent, place):
        if self.weather == "storm" or self.step < self.festival_until or not (6 <= self.minute // 60 < 18):
            return []
        if place == "nets" and agent.role in ("fisher", "boatwright"):
            return ["mended_mesh", "knotted_rope"]
        if agent.role == "fisher" and place in ("jetty", "shore"):
            if self.pantry["net_condition"] == "torn":
                return ["torn_mesh"]
            return ["silver_fish", "mackerel", "empty_net", "torn_mesh"]
        if agent.role == "gardener" and place == "garden":
            return ["first_basil", "mint_bundle", "wilted_seedlings"]
        if agent.role == "boatwright" and place == "shore":
            return ["sealed_hull", "sound_boat", "leaking_boat"]
        return []

    def _work(self, agent, place, requested=None):
        options = self._result_options(agent, place)
        # Useful work is common; failures still have material consequences.
        result = requested if requested in options else self.rng.choice(options[:2] * 3 + options[2:])
        detail, success = {
            "silver_fish": ("caught a silver fish", True),
            "mackerel": ("landed a striped mackerel", True),
            "empty_net": ("hauled up an empty net", False),
            "torn_mesh": ("found a torn mesh in the village net", False),
            "first_basil": ("picked the first basil from a new stem", True),
            "mint_bundle": ("gathered a fragrant bundle of mint", True),
            "wilted_seedlings": ("found the herb seedlings wilted", False),
            "sealed_hull": ("sealed a leaking hull with fresh pitch", True),
            "sound_boat": ("tested a boat that floated soundly", True),
            "leaking_boat": ("found a boat that would not float", False),
            "mended_mesh": ("mended the torn mesh of the village net", True),
            "knotted_rope": ("secured the net with a new rope knot", True),
        }[result]
        if result in ("silver_fish", "mackerel"):
            self.pantry["fish"] += 1
        elif result in ("first_basil", "mint_bundle"):
            self.pantry["herbs"] += 1
        elif result == "torn_mesh":
            self.pantry["net_condition"] = "torn"
        elif result in ("mended_mesh", "knotted_rope"):
            self.pantry["net_condition"] = "sound"
        elif result == "leaking_boat":
            self.pantry["boat_condition"] = "leaking"
        elif result in ("sealed_hull", "sound_boat"):
            self.pantry["boat_condition"] = "sound"
        agent.work_count += 1
        agent.result = result
        agent.mood = "content" if success else "worried"
        text = f"{agent.name} {detail}."
        agent.thought = agent.thought or text
        self.event(text, "work", [agent.id])
        key = f"{self.day}:{agent.id}:{place}"
        summary = self.daily_work.setdefault(key, {"day": self.day, "agent": agent.id, "place": place, "results": {}})
        summary["results"][result] = summary["results"].get(result, 0) + 1
        if success:
            self._progress_goal(agent, "work", place=place)

    def _supper(self):
        need_fish, need_herbs = len(self.agents), max(1, (len(self.agents) + 2) // 3)
        fish, herbs = min(need_fish, self.pantry["fish"]), min(need_herbs, self.pantry["herbs"])
        self.pantry["fish"] -= fish
        self.pantry["herbs"] -= herbs
        missing = {"fish": need_fish - fish, "herbs": need_herbs - herbs}
        empty = []
        for agent in self.agents.values():
            needed_results = {"fisher": ("silver_fish", "mackerel"), "gardener": ("first_basil", "mint_bundle")}.get(agent.role, ())
            contributed = sum(sum(row["results"].get(r, 0) for r in needed_results) for row in self.daily_work.values() if row["day"] == self.day and row["agent"] == agent.id)
            if needed_results and not contributed:
                empty.append(agent.name)
        shortage = missing["fish"] or missing["herbs"]
        text = (f"Supper on day {self.day} used {fish} fish and {herbs} herb bundles; "
                + (f"the table was short by {missing['fish']} fish and {missing['herbs']} herbs" if shortage else "everyone ate well")
                + (f", and {', '.join(empty)} came home empty-handed" if shortage and empty else "") + ".")
        item = {"day": self.day, "fish": fish, "herbs": herbs, "missing": missing, "empty_handed": empty, "text": text}
        self.suppers.append(item)
        self.last_miss = {}
        if missing["fish"]:
            self.last_miss["fisher"] = "Yesterday’s jetty left supper short; try the shore for 3 fish"
        if missing["herbs"]:
            self.last_miss["gardener"] = "Yesterday’s herb rack was short; gather 3 bundles for supper"
        if self.pantry["boat_condition"] == "leaking":
            self.last_miss["boatwright"] = "Yesterday’s boat would not float; repair and test 3 hulls"
        self.event(text, "supper", self.agents)
        for agent in self.agents.values():
            agent.mood = "worried" if shortage else "content"

    def _reflect(self, day):
        supper = next((s for s in reversed(self.suppers) if s["day"] == day), None)
        if supper:
            first = supper["text"]
        else:
            first = f"Day {day} ended before anyone laid the supper table."
        rows = [row for row in self.daily_work.values() if row["day"] == day]
        failures = sum(row["results"].get(key, 0) for row in rows for key in ("empty_net", "torn_mesh", "wilted_seedlings", "leaking_boat"))
        catches = sum(row["results"].get(key, 0) for row in rows for key in ("silver_fish", "mackerel"))
        second = f"The village landed {catches} fish and met {failures} setbacks; " + ("tomorrow’s work will answer the shortages." if supper and any(supper["missing"].values()) else "the shared table is what we will remember.")
        item = self.event(first + " " + second, "reflection", self.agents)
        if item:
            item["reflected_day"] = day
            self.reflections.append(dict(item))
        # Detailed repeated chores collapse into one factual row per worker/place.
        for row in rows:
            agent = self.residents[row["agent"]]
            counts = ", ".join(f"{count} {key.replace('_', ' ')}" for key, count in row["results"].items())
            text = f"Day {day}: {agent.name} worked at {self.world.places[row['place']]['name']}: {counts}."
            if agent.id in self.agents:
                self.event(text, "work_summary", [agent.id])
        self.events = [e for e in self.events if e["day"] > day or e["kind"] not in ("work", "activity")]
        for agent in self.residents.values():
            agent.memory = [e for e in agent.memory if e["day"] > day or e["kind"] not in ("work", "activity")]

    def _speech_options(self, agent):
        hour = self.minute // 60
        if hour < 6 or hour >= 23:
            return []
        facts = [m for m in agent.memory[-8:] if m["kind"] in ("work", "supper", "weather", "festival", "reflection")]
        if not facts:
            return []
        fact = facts[-1]["text"]
        craft = {"fisher": "nets", "gardener": "herbs", "boatwright": "boats"}[agent.role]
        period = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return [f"This {period}, with the {craft} on my mind, I remember: {fact}"]

    def _promise_sentence(self, agent, other):
        task = {
            "fisher": "bring fish to the next supper",
            "gardener": "bring herbs to the next supper",
            "boatwright": "keep the village boats afloat",
        }[agent.role]
        return f"{agent.name} promised {other.name} to {task}."

    def _promise_options(self, agent):
        if not self._speech_options(agent):
            return []
        return [
            self._promise_sentence(agent, other)
            for other in self.agents.values()
            if other.id != agent.id
        ]

    def _keep_promise(self, agent, promise):
        for other in self.agents.values():
            if other.id != agent.id and promise.startswith(f"{agent.name} promised {other.name} "):
                agent.relationships[other.id] = other.relationships[agent.id] = promise
                self.event(promise, "promise", [agent.id, other.id])
                return

    def _converse(self):
        villagers = list(self.agents.values())
        pairs = [(first, other) for i, first in enumerate(villagers) for other in villagers[i + 1:]]
        def priority(pair):
            return -sum(goal["id"] == "social" and not goal["completed"] and goal["partner"] == partner.id
                        for agent, partner in (pair, pair[::-1]) for goal in agent.goals)
        for first, other in sorted(pairs, key=priority):
            if min(self.step - first.last_chat, self.step - other.last_chat) < 8:
                continue
            if "sleeping" in (first.activity, other.activity):
                continue
            if abs(first.x - other.x) + abs(first.y - other.y) > 2:
                continue
            lines = self._speech_options(first)
            if not lines:
                continue
            # Decide the shared fact before recording the line. The new memory
            # can push the fact that allowed speech out of the recent window.
            if self.weather == "storm":
                fact = f"{first.name} and {other.name} sat out the rain together."
            elif self.suppers and self.minute >= 1080:
                fact = f"{first.name} and {other.name} shared supper on day {self.day}."
            else:
                fact = self._promise_sentence(first, other)
            line = lines[0]
            event = self.event(f"{first.name} to {other.name}: “{line}”", "conversation", [first.id, other.id])
            if not event:
                continue
            first.speech = line
            first.last_chat = other.last_chat = self.step
            first.relationships[other.id] = other.relationships[first.id] = fact
            self._progress_goal(first, "social", partner=other.id)
            self._progress_goal(other, "social", partner=first.id)

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
            "pantry": dict(self.pantry),
            "suppers": copy.deepcopy(self.suppers),
            "reflections": copy.deepcopy(self.reflections),
            "visitor": copy.deepcopy(self.visitor),
            "work_summaries": copy.deepcopy(list(self.daily_work.values())),
            "agents": [asdict(a) for a in self.agents.values()],
            "events": self.events[-80:],
            "event_count": self.next_event_id,
        }
