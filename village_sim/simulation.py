"""Pure simulation: perceive → retrieve → plan → reflect → execute.

All model state belongs here. External providers submit optional decisions; the
clock and default decisions never wait for them. Snapshots contain only JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import copy
import random
from typing import Callable

from .cognition.memory import MemoryStore, memory_view
from .cognition import planning, conversations, reflection
from . import visitors, survival
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
    cognitive_plan: dict | None = None
    cognitive_revision: int = 0
    replan_reason: str | None = "no_plan"
    trust: dict[str, float] = field(default_factory=dict)
    last_reflection_step: int = -4
    reflection_cursor: int = 0
    reflection_reason: str | None = None
    food: float = 100
    water: float = 100
    health: float = 100
    collapsed_until: int = 0


class Simulation:
    def __init__(self, count: int = 3, seed: int = 7, decision: Callable = decide,
                 cognition_mode: str = "rules"):
        if cognition_mode not in planning.MODES:
            raise ValueError("Choose rules or hybrid cognition")
        self.world = World()
        self.seed = seed
        self.rng = random.Random(seed)
        self.decision = decision
        self.cognition_mode = cognition_mode
        self.conversations: list[dict] = []
        self.commitments: list[dict] = []
        self._memory_stores: dict[str, MemoryStore] = {}
        self.step = 0
        self.weather = "clear"
        self.weather_until = 0
        self.festival_until = 0
        self.agents: dict[str, Agent] = {}
        self.residents: dict[str, Agent] = {}
        self.pending: dict[str, Action] = {}
        self.events: list[dict] = []
        self.next_event_id = 0
        self.pantry = {"fish": 0, "herbs": 0, "water": 3 * count, "boat_condition": "sound",
                       "net_condition": "sound", "hearth_lit": True}
        self.water_sources = survival.initial_sources()
        self.suppers: list[dict] = []
        self.reflections: list[dict] = []
        self.visitor: dict | None = None
        # Visiting agents (outside programs). Empty unless an operator enables them.
        self.visiting: dict = visitors.initial_state()
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

    def event(self, text: str, kind: str, agent_ids=(), *, source_ids=(), **details):
        # Text describes an occurrence, not its identity. Repeated catches,
        # promises and storms must each receive their own durable event ID.
        item = {
            "id": self.next_event_id,
            "step": self.step,
            "day": self.day,
            "time": f"{self.minute // 60:02}:{self.minute % 60:02}",
            "text": text,
            "kind": kind,
            "agents": list(agent_ids),
            "timestamp": self.total_minutes,
            "source_ids": list(source_ids),
            **details,
        }
        self.next_event_id += 1
        self.events.append(item)
        for aid in item["agents"]:
            self.memory_store(aid).add(item)
            if kind in ("promise", "weather", "festival", "goal", "supper", "insight", "trust") or details.get("significant"):
                self.request_replan(aid, kind)
        return item

    def memory_store(self, aid):
        records = self.residents[aid].memory
        store = self._memory_stores.get(aid)
        if store is None or store.records is not records:
            store = self._memory_stores[aid] = MemoryStore(records)
        return store

    def request_replan(self, aid, reason):
        if self.cognition_mode != "hybrid":
            return
        agent = self.residents[aid]
        agent.cognitive_revision += 1
        if reason != "schedule_changed" or agent.replan_reason not in {
                "insight", "trust", "commitment_missed", "commitment_completed"}:
            agent.replan_reason = reason
        if agent.cognitive_plan and agent.cognitive_plan["status"] == "active":
            agent.cognitive_plan.update(status="invalidated", reason=reason, updated_step=self.step)

    def set_cognition_mode(self, mode):
        if mode not in planning.MODES:
            raise ValueError("Choose rules or hybrid cognition")
        if mode == self.cognition_mode:
            return
        self.cognition_mode = mode
        conversations.update(self, advance=False)
        self.pending.clear()
        for agent in self.residents.values():
            agent.cognitive_revision += 1
            agent.replan_reason = "mode_changed"
            if agent.cognitive_plan and agent.cognitive_plan["status"] == "active":
                agent.cognitive_plan.update(status="invalidated", reason="mode_changed", updated_step=self.step)

    def set_population(self, count: int):
        if type(count) is not int or not 1 <= count <= len(PERSONAS):
            raise ValueError("Population must be an integer between 1 and 8")
        wanted = {p[0] for p in PERSONAS[:count]}
        changed = set(self.agents) != wanted
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
            if changed:
                self.request_replan(agent.id, "population_changed")
        if changed:
            conversations.update(self, advance=False)

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

    @staticmethod
    def agent_view(agent, memory_limit=64):
        view = {item.name: copy.deepcopy(getattr(agent, item.name))
                for item in fields(agent) if item.name != "memory"}
        view["memory"] = memory_view(agent.memory[-memory_limit:] if memory_limit else [],
                                     limit=memory_limit, characters=24000)
        for memory in view["memory"]:
            memory.update(embedding=None, embedding_model=None)
        view["memory_count"] = len(agent.memory)
        return view

    def observe(self, aid: str, memory_query: str | None = None, *, query_embedding=None,
                embedding_model=None) -> dict:
        if memory_query is not None and (not isinstance(memory_query, str) or len(memory_query) > 500):
            raise ValueError("Memory query must be text, at most 500 characters")
        agent = self.agents[aid]
        nearby = [
            other
            for other in self.agents.values()
            if other.id != aid and abs(other.x - agent.x) + abs(other.y - agent.y) <= 4
        ]
        query = memory_query if memory_query is not None else " ".join([
            agent.activity, self.world.places[agent.destination]["name"],
            *(goal["title"] for goal in agent.goals if not goal["completed"]),
            *(other.name for other in nearby),
            *agent.relationships.values(),
        ])
        query = query[:500]
        relevant = self.memory_store(aid).retrieve(query, now=self.total_minutes,
                                                  query_embedding=query_embedding,
                                                  embedding_model=embedding_model)
        observation = {
            "step": self.step,
            "day": self.day,
            "minute": self.minute,
            "weather": self.weather,
            "festival": self.step < self.festival_until,
            "pantry": dict(self.pantry),
            **self.survival_view(),
            "food": agent.food, "water": agent.water, "health": agent.health,
            "last_supper": copy.deepcopy(self.suppers[-1]) if self.suppers else None,
            "agent": self.agent_view(agent),
            "cognition_mode": self.cognition_mode,
            "decision_contract": planning.CONTRACT if self.cognition_mode == "hybrid" else "voice-v1",
            "cognitive_revision": agent.cognitive_revision,
            "replan_reason": agent.replan_reason,
            "conversation": conversations.context(self, aid),
            "commitments": [copy.deepcopy(c) for c in self.commitments
                            if c["status"] == "scheduled" and aid in c["terms"]["participants"]],
            "trust": {partner: agent.trust[partner] for partner in sorted(agent.trust)},
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
            "relevant_memories": memory_view(relevant),
            "recent_memories": memory_view(agent.memory[-12:], characters=3000),
            "memory_query": query,
            "places": self.world.config["places"],
        }
        suggested = decide(agent, observation)
        observation["suggested_action"] = asdict(suggested)
        observation["allowed_actions"] = [{"place": suggested.place, "activity": suggested.activity}]
        if self.cognition_mode == "hybrid":
            observation["allowed_actions"] = planning.legal_actions(agent, observation)
            observation["preferred_helpers"] = {
                task: helper.id if (helper := reflection.choose_helper(self, agent, task)) else None
                for task in ("talk", "repair_nets", "repair_boat", "gather_herbs", "fish")
            }
            observation["reflection"] = reflection.context(self, aid)
        observation["result_options"] = self._result_options(agent, suggested.place)
        observation["mood_options"] = ["content", "hopeful", "worried", "tired"]
        observation["promise_options"] = self._promise_options(agent)
        observation["speech_options"] = self._speech_options(agent)
        return observation

    def submit(self, aid: str, data: dict):
        if aid not in self.agents:
            raise ValueError("Unknown villager")
        if survival.collapsed(self.agents[aid]):
            raise ValueError("This villager is collapsed and cannot act until recovery")
        if type(data.get("step")) is not int or data["step"] != self.step:
            raise ValueError("Decision is stale; fetch a fresh observation")
        if data.get("contract") == conversations.CONTRACT:
            conversations.apply_turn(self, aid, data)
            return
        if data.get("contract") == reflection.CONTRACT:
            reflection.apply_turn(self, aid, data)
            return
        observation = self.observe(aid)
        if data.get("contract") == planning.CONTRACT:
            if self.cognition_mode != "hybrid":
                raise ValueError("Enable hybrid cognition before submitting plans")
            if type(data.get("revision")) is not int or data["revision"] != self.agents[aid].cognitive_revision:
                raise ValueError("Plan revision is stale; fetch a fresh observation")
            agent = self.agents[aid]
            proposal = planning.validate_proposal(data, {**observation, "agent": {"memory": agent.memory}})
            for step in proposal["steps"]:
                start, end = (agent.x, agent.y), self.world.point(step["location"])
                if start != end and not self.world.path(start, end):
                    raise ValueError("Plan destination is unreachable")
            agent.cognitive_revision += 1
            agent.cognitive_plan = planning.start_plan(proposal, revision=agent.cognitive_revision, step=self.step)
            agent.replan_reason = None
            self.pending.pop(aid, None)
            self.event(f"{agent.name} plans to {proposal['goal']}", "plan", [aid], source_ids=proposal["memory_ids"])
            return
        place = data.get("place", observation["suggested_action"]["place"])
        activity = data.get("activity", observation["suggested_action"]["activity"])
        survival.validate_activity(place, activity)
        if (place, activity) != (observation["suggested_action"]["place"], observation["suggested_action"]["activity"]):
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

    def _with_voice(self, agent, observation, voice):
        ruled = self.decision(agent, observation)
        if voice is None:
            return ruled

        def keep(field):
            value = getattr(voice, field)
            return value if value in observation.get(field + "_options", []) else None

        thought = voice.thought
        if not isinstance(thought, str) or len(thought) > 200:
            thought = None
        return Action(
            ruled.place,
            ruled.activity,
            keep("speech"),
            keep("result"),
            keep("mood"),
            keep("promise"),
            thought.strip() if isinstance(thought, str) else None,
        )

    def adopt_model_decision(self, aid: str, action: dict):
        """Adopt an explicit hybrid plan or fit legacy voice slots to the rules."""
        if action.get("contract") == conversations.CONTRACT:
            conversations.apply_turn(self, aid, action)
            return action
        observation = self.observe(aid)
        if action.get("contract") == planning.CONTRACT:
            self.submit(aid, {**action, "step": self.step, "revision": observation["cognitive_revision"]})
            return action
        suggested = observation["suggested_action"]
        fitted = {
            "step": self.step,
            "place": suggested["place"],
            "activity": suggested["activity"],
        }
        for field in ("result", "mood", "promise", "speech"):
            value = action.get(field)
            fitted[field] = value if value in observation.get(field + "_options", []) else None
        thought = action.get("thought")
        if isinstance(thought, str):
            thought = thought.strip()[:200] or None
        else:
            thought = None
        fitted["thought"] = thought
        self.submit(aid, fitted)
        return fitted

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
        conversations.update(self, advance=False)

    def tick(self):
        centers_before = survival.centers(self)
        previous_day = self.day
        previous_hour = self.minute // 60
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
        day_ended = self.day != previous_day
        if day_ended:
            self._reflect(previous_day)
        for agent in self.agents.values():
            agent.speech = None
            agent.thought = None
            agent.result = None
            if day_ended:
                agent.plan = daily_plan(agent)
                self._refresh_goals(agent)
            if previous_hour != self.minute // 60 and self.minute // 60 in (0, 6, 10, 13, 15, 18, 21, 23):
                self.request_replan(agent.id, "schedule_changed")
        survival.tick(self)
        if self.minute >= 18 * 60 and not any(s["day"] == self.day for s in self.suppers):
            self._supper()
        conversations.update(self)
        for agent in self.agents.values():
            if survival.collapsed(agent):
                continue
            if conversations.active(self, agent.id):
                agent.activity = "in conversation"
                continue
            observation = self.observe(agent.id)
            action = self.pending.pop(agent.id, None)
            # The rules choose where the body goes. A connected brain can still
            # leave its mood, thought, speech, promise and work result.
            suggested = observation["suggested_action"]
            if (
                action is None
                or self.weather == "storm"
                or self.step < self.festival_until
                or action.place != suggested["place"]
                or action.activity != suggested["activity"]
            ):
                action = self._with_voice(agent, observation, action)
            cognitive = agent.cognitive_plan
            planned = False
            if self.cognition_mode == "hybrid" and cognitive and cognitive["status"] == "active":
                try:
                    if self.step - cognitive["created_step"] > 96:
                        raise ValueError("Plan expired")
                    action = planning.action_for(cognitive, observation)
                    start, end = (agent.x, agent.y), self.world.point(action.place)
                    if start != end and not self.world.path(start, end):
                        raise ValueError("Plan destination is unreachable")
                    planned = True
                except ValueError:
                    self.request_replan(agent.id, "action_unavailable")
                    action = self._with_voice(agent, observation, None)
            meeting = conversations.meeting_action(self, agent.id)
            if meeting:
                if planned:
                    self.request_replan(agent.id, "commitment_due")
                action, planned = meeting, False
            urgent = survival.personal_action(agent, observation)
            if urgent and action.activity != urgent.activity:
                if planned:
                    self.request_replan(agent.id, "survival_need")
                action, planned, meeting = urgent, False, None
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
            productive = not planned or cognitive["steps"][cognitive["index"]]["action"] in ("work", "routine")
            if meeting:
                productive = False  # Shared work runs after both residents move.
            handled = arrived and survival.execute(self, agent, action.place, action.activity)
            if arrived and not handled and productive and self.step - agent.last_work >= 4 and self._result_options(agent, action.place):
                agent.last_work = self.step
                self._work(agent, action.place, action.result)
            if planned and arrived and cognitive["status"] == "active":
                cognitive["remaining"] -= 1
                cognitive["updated_step"] = self.step
                if cognitive["remaining"] == 0:
                    cognitive["index"] += 1
                    if cognitive["index"] == len(cognitive["steps"]):
                        cognitive["status"] = "completed"
                        self.event(f"{agent.name} completed a plan: {cognitive['goal']}", "plan", [agent.id])
                        self.request_replan(agent.id, "plan_completed")
                    else:
                        cognitive["remaining"] = cognitive["steps"][cognitive["index"]]["duration_minutes"] // 15
            if agent.health < 30:
                agent.mood = "tired"
        conversations.attend(self)
        self._converse()
        reflection.consider(self, day_end=day_ended)
        reflection.adopt_help_plans(self)
        visitors.tick(self, day_ended=day_ended)
        for resource, center in survival.centers(self).items():
            if center["low"] and (day_ended or not centers_before[resource]["low"]):
                self.event(f"The {resource} collection center is below one day's supply for {len(self.agents)} villagers.",
                           "survival", self.agents, significant=True)
        return self.snapshot()

    def _result_options(self, agent, place):
        if survival.collapsed(agent):
            return []
        if place == "garden" and agent.food <= 40:
            return ["first_basil", "mint_bundle", "wilted_seedlings"]
        if self.weather == "storm" or self.step < self.festival_until or not (6 <= self.minute // 60 < 18):
            return []
        if place == "nets":
            return ["mended_mesh", "knotted_rope"]
        if place == "jetty" or (agent.role == "fisher" and place == "shore"):
            if self.pantry["net_condition"] == "torn":
                return ["torn_mesh"]
            return ["silver_fish", "mackerel", "empty_net", "torn_mesh"]
        if place == "garden":
            return ["first_basil", "mint_bundle", "wilted_seedlings"]
        if agent.role == "boatwright" and place == "shore":
            return ["sealed_hull", "sound_boat", "leaking_boat"]
        return []

    def _work(self, agent, place, requested=None):
        options = self._result_options(agent, place)
        if not options:
            return
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
        self.event(text, "work", [agent.id], place=place,
                   collection_center=survival.FOOD_CENTER if result in ("silver_fish", "mackerel", "first_basil", "mint_bundle") else None,
                   significant=result in ("torn_mesh", "leaking_boat", "wilted_seedlings"))
        key = f"{self.day}:{agent.id}:{place}"
        summary = self.daily_work.setdefault(key, {"day": self.day, "agent": agent.id, "place": place, "results": {}})
        summary["results"][result] = summary["results"].get(result, 0) + 1
        if success:
            self._progress_goal(agent, "work", place=place)

    def _supper(self):
        diners = sorted((a for a in self.agents.values() if not survival.collapsed(a)), key=lambda a: (a.food, a.id))
        need_fish, need_herbs = len(self.agents), max(1, (len(self.agents) + 2) // 3)
        fish, herbs = min(len(diners), self.pantry["fish"]), min(need_herbs, len(diners), self.pantry["herbs"])
        self.pantry["fish"] -= fish
        self.pantry["herbs"] -= herbs
        for agent in diners[:fish]:
            agent.food = survival.clamp(agent.food + survival.FISH_FOOD)
        for agent in sorted(diners, key=lambda a: (a.food, a.id))[:herbs]:
            agent.food = survival.clamp(agent.food + survival.HERB_FOOD)
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
        sources = [event["id"] for event in self.events if event["day"] == day
                   and event["kind"] in ("supper", "work", "goal")]
        item = self.event(first + " " + second, "reflection", self.agents,
                          source_ids=sources, reflected_day=day)
        if item:
            self.reflections.append(dict(item))
        # Summaries supplement episodes; reflection never erases experience.
        for row in rows:
            agent = self.residents[row["agent"]]
            counts = ", ".join(f"{count} {key.replace('_', ' ')}" for key, count in row["results"].items())
            text = f"Day {day}: {agent.name} worked at {self.world.places[row['place']]['name']}: {counts}."
            sources = [event["id"] for event in self.events if event["day"] == day
                       and event["kind"] == "work" and agent.id in event["agents"]
                       and event.get("place", row["place"]) == row["place"]]
            self.event(text, "work_summary", [agent.id], source_ids=sources)

    def _speech_options(self, agent):
        if survival.collapsed(agent):
            return []
        hour = self.minute // 60
        if hour < 6 or hour >= 23:
            return []
        facts = [m for m in agent.memory[-8:] if m["kind"] in ("work", "supper", "weather", "festival", "reflection", "survival", "collapse", "recovery")]
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
        if self.cognition_mode == "hybrid":
            conversations.start_nearby(self)
            return
        villagers = list(self.agents.values())
        pairs = [(first, other) for i, first in enumerate(villagers) for other in villagers[i + 1:]]
        def priority(pair):
            return -sum(goal["id"] == "social" and not goal["completed"] and goal["partner"] == partner.id
                        for agent, partner in (pair, pair[::-1]) for goal in agent.goals)
        for first, other in sorted(pairs, key=priority):
            if survival.collapsed(first) or survival.collapsed(other):
                continue
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
            if fact == self._promise_sentence(first, other):
                self.event(fact, "promise", [first.id, other.id])
            self._progress_goal(first, "social", partner=other.id)
            self._progress_goal(other, "social", partner=first.id)

    def survival_observation(self):
        return {"step": self.step, "pantry": self.pantry, "water_sources": self.water_sources,
                "places": self.world.config["places"]}

    def survival_view(self):
        return {
            "villager_count": len(self.agents),
            "collection_centers": survival.centers(self),
            "water_sources": copy.deepcopy(self.water_sources),
            "present_villagers": [
                {"id": a.id, "name": a.name, "x": a.x, "y": a.y,
                 "food": a.food, "water": a.water, "health": a.health,
                 "collapsed": survival.collapsed(a),
                 "busy": bool(conversations.active(self, a.id) or conversations.meeting_action(self, a.id)),
                 "work_completed": any(g["id"] == "work" and g["completed"] for g in a.goals)}
                for a in self.agents.values()],
        }

    def snapshot(self):
        return {
            "schema_version": 1,
            "village": self.world.config["name"],
            "seed": self.seed,
            "cognition_mode": self.cognition_mode,
            "step": self.step,
            "day": self.day,
            "minute": self.minute,
            "time": f"{self.minute // 60:02}:{self.minute % 60:02}",
            "weather": self.weather,
            "festival": self.step < self.festival_until,
            "pantry": dict(self.pantry),
            **self.survival_view(),
            "suppers": copy.deepcopy(self.suppers[-7:]),
            "reflections": copy.deepcopy(self.reflections[-7:]),
            "visitor": copy.deepcopy(self.visitor),
            "visiting": visitors.snapshot(self),
            "work_summaries": copy.deepcopy(list(self.daily_work.values())[-32:]),
            "conversations": copy.deepcopy(self.conversations[-16:]),
            "commitments": copy.deepcopy(self.commitments[-32:]),
            "agents": [self.agent_view(a) for a in self.agents.values()],
            "events": memory_view(self.events[-80:], limit=80, characters=24000),
            "event_count": self.next_event_id,
        }
