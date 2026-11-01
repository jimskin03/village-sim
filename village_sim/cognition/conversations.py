"""Bounded two-person dialogue and jointly accepted, executable appointments."""

import copy
import math

from ..decisions import Action
from .memory import memory_view

CONTRACT = "conversation-v1"
TASKS = {
    "talk": ("bench", "fire", "shore"),
    "repair_nets": ("nets",),
    "repair_boat": ("shore",),
    "gather_herbs": ("garden",),
    "fish": ("jetty", "shore"),
}
CRAFTS = {"repair_nets": ("fisher", "boatwright"), "repair_boat": ("boatwright",),
          "gather_herbs": ("gardener",), "fish": ("fisher",)}


def active(sim, aid):
    return next((c for c in reversed(sim.conversations)
                 if c["status"] == "active" and aid in c["participants"]), None)


def speaker(conversation):
    return conversation["participants"][len(conversation["transcript"]) % 2]


def travel(sim, aid, place, origin=None):
    agent = sim.agents[aid]
    start = origin or (agent.x, agent.y)
    end = sim.world.point(place)
    path = sim.world.path(start, end)
    if start != end and not path:
        raise ValueError("Meeting place is unreachable")
    return math.ceil(max(0, len(path) - 1) / 2) * 15


def terms_shape(terms, participants, places):
    if (not isinstance(terms, dict)
            or set(terms) != {"participants", "place", "task", "start_minute", "duration_minutes"}
            or terms["participants"] != participants
            or not isinstance(terms["task"], str) or terms["task"] not in TASKS
            or terms["place"] not in TASKS[terms["task"]] or terms["place"] not in places
            or type(terms["start_minute"]) is not int or terms["start_minute"] < 0 or terms["start_minute"] % 15
            or type(terms["duration_minutes"]) is not int
            or not 15 <= terms["duration_minutes"] <= 180 or terms["duration_minutes"] % 15):
        raise ValueError("Invalid commitment terms")
    start = terms["start_minute"] % 1440
    end = start + terms["duration_minutes"]
    if start < 360 or end > (1380 if terms["task"] == "talk" else 1080):
        raise ValueError("Meetings must fit waking hours; craft work ends before supper")
    return copy.deepcopy(terms)


def validate_terms(sim, conversation, terms):
    terms = terms_shape(terms, conversation["participants"], sim.world.places)
    start, duration = terms["start_minute"], terms["duration_minutes"]
    if not sim.total_minutes < start <= sim.total_minutes + 1440:
        raise ValueError("Choose a future meeting within one day")
    if sim.weather == "storm" or sim.step < sim.festival_until:
        raise ValueError("Villagers are unavailable during a storm or festival")
    people = terms["participants"]
    if any(aid not in sim.agents for aid in people):
        raise ValueError("A participant has left the village")
    if terms["task"] in CRAFTS and not any(sim.agents[aid].role in CRAFTS[terms["task"]] for aid in people):
        raise ValueError("This task needs someone with the appropriate craft")
    for aid in people:
        if sim.total_minutes + travel(sim, aid, terms["place"]) + 15 > start:
            raise ValueError("Allow enough time to travel to the meeting")
        for existing in sim.commitments:
            if existing["status"] != "scheduled" or aid not in existing["terms"]["participants"]:
                continue
            other = existing["terms"]
            other_start, other_end = other["start_minute"], other["start_minute"] + other["duration_minutes"]
            gap = travel(sim, aid, terms["place"], sim.world.point(other["place"]))
            if not (start + duration + gap <= other_start or other_end + gap <= start):
                raise ValueError("The meeting conflicts with an accepted commitment or its travel time")
    return terms


def context(sim, aid):
    conversation = active(sim, aid)
    if (conversation is None or speaker(conversation) != aid or not available(sim, conversation)
            or sim.step - conversation["created_step"] >= 8):
        return None
    agent = sim.agents[aid]
    query = conversation["topic"] + " " + " ".join(sim.residents[p].name for p in conversation["participants"])
    # No observation/world journal or partner memory enters this private context.
    return {
        "contract": CONTRACT, "conversation_id": conversation["id"],
        "turn": len(conversation["transcript"]), "step": sim.step, "now": sim.total_minutes,
        "speaker": aid, "persona": agent.persona, "goals": copy.deepcopy(agent.goals),
        "daily_plan": copy.deepcopy(agent.plan),
        "commitments": [copy.deepcopy(c) for c in sim.commitments
                        if c["status"] == "scheduled" and aid in c["terms"]["participants"]],
        "conversation": copy.deepcopy(conversation),
        "trust": {partner: agent.trust[partner] for partner in sorted(agent.trust)},
        "memories": memory_view(sim.memory_store(aid).retrieve(query, now=sim.total_minutes, limit=12), characters=6000),
        "tasks": TASKS, "earliest_start": sim.total_minutes + 15 + max(
            travel(sim, person, "bench") for person in conversation["participants"]),
    }


def validate_turn(data, request):
    if (not isinstance(data, dict) or data.get("contract") != CONTRACT
            or type(data.get("conversation_id")) is not int or data["conversation_id"] != request["conversation_id"]
            or type(data.get("turn")) is not int or data["turn"] != request["turn"]
            or data.get("move") not in ("say", "propose", "counter", "accept", "decline")
            or not isinstance(data.get("speech"), str) or not 1 <= len(data["speech"].strip()) <= 240):
        raise ValueError("Invalid or stale conversation turn")
    move = data["move"]
    conversation = request["conversation"]
    terms = data.get("terms")
    if move in ("propose", "counter", "accept"):
        terms = terms_shape(terms, conversation["participants"], {p for values in TASKS.values() for p in values})
    elif terms is not None:
        raise ValueError("Only proposals, counters and acceptance carry terms")
    if move == "propose" and conversation["offer"] is not None:
        raise ValueError("Counter the existing offer instead")
    if move in ("counter", "accept") and (conversation["offer"] is None or conversation["offered_by"] == request["speaker"]):
        raise ValueError("There is no partner offer to answer")
    if move == "accept" and terms != conversation["offer"]:
        raise ValueError("Acceptance must repeat exactly the offered terms")
    return {"contract": CONTRACT, "conversation_id": data["conversation_id"], "turn": data["turn"],
            "move": move, "speech": data["speech"].strip(), "terms": terms}


def finish(sim, conversation, status, reason):
    if conversation["status"] != "active":
        return
    conversation.update(status=status, reason=reason)
    people = conversation["participants"]
    names = " and ".join(sim.residents[p].name for p in people)
    sim.event(f"{names} ended their conversation: {reason}.", "conversation", people,
              source_ids=[turn["memory_id"] for turn in conversation["transcript"]])
    for aid in people:
        sim.residents[aid].last_chat = sim.step


def available(sim, conversation):
    people = conversation["participants"]
    if sim.cognition_mode != "hybrid" or sim.weather == "storm" or sim.step < sim.festival_until:
        return False
    if any(aid not in sim.agents for aid in people) or not 6 <= sim.minute // 60 < 23:
        return False
    first, other = (sim.agents[aid] for aid in people)
    return abs(first.x - other.x) + abs(first.y - other.y) <= 2


def apply_turn(sim, aid, data):
    request = context(sim, aid)
    if request is None:
        raise ValueError("It is not this villager's turn")
    conversation = active(sim, aid)
    if not available(sim, conversation) or sim.step - conversation["created_step"] >= 8:
        raise ValueError("The conversation is no longer available")
    turn = validate_turn(data, request)
    if turn["terms"] is not None:
        validate_terms(sim, conversation, turn["terms"])
    # All checks precede speech, memories and either participant's schedule.
    move, terms = turn["move"], turn["terms"]
    item = sim.event(f"{sim.agents[aid].name}: “{turn['speech']}”", "conversation", conversation["participants"])
    conversation["transcript"].append({"speaker": aid, "step": sim.step, "move": move,
                                        "speech": turn["speech"], "terms": terms, "memory_id": item["id"]})
    sim.agents[aid].speech = turn["speech"]
    if move in ("propose", "counter"):
        conversation.update(offer=terms, offered_by=aid)
    elif move == "accept":
        commitment = {"id": len(sim.commitments) + 1, "conversation_id": conversation["id"],
                      "terms": copy.deepcopy(terms), "status": "scheduled", "attended": [], "reason": None}
        sim.commitments.append(commitment)
        names = " and ".join(sim.residents[p].name for p in terms["participants"])
        fact = f"{names} agreed to {terms['task'].replace('_', ' ')} at {terms['place']} at minute {terms['start_minute']} for {terms['duration_minutes']} minutes."
        sim.event(fact, "promise", terms["participants"], source_ids=[t["memory_id"] for t in conversation["transcript"]],
                  task=terms["task"])
        first, second = terms["participants"]
        sim.residents[first].relationships[second] = sim.residents[second].relationships[first] = fact
        finish(sim, conversation, "completed", "meeting agreed")
    elif move == "decline":
        finish(sim, conversation, "declined", "invitation declined")
    if len(conversation["transcript"]) >= 6:
        finish(sim, conversation, "completed", "turn limit reached")
    if conversation["status"] != "active":
        first, second = conversation["participants"]
        sim._progress_goal(sim.residents[first], "social", partner=second)
        sim._progress_goal(sim.residents[second], "social", partner=first)


def _goal_aligned(sim, aid, partner, terms):
    agent = sim.agents[aid]
    task, place = terms["task"], terms["place"]
    for goal in agent.goals:
        if goal["completed"]:
            continue
        if goal["id"] == "social" and goal["partner"] == partner and task == "talk":
            return True
        places = {
            "fisher": {"fish": ("jetty", "shore"), "repair_nets": ("nets",)},
            "gardener": {"gather_herbs": ("garden",)},
            "boatwright": {"repair_boat": ("shore",), "repair_nets": ("nets",)},
        }[agent.role]
        if goal["id"] == "work" and place in places.get(task, ()) and goal["place"] == place:
            return True
    return False


def acceptance(sim, aid, terms):
    """Fallback agrees only when the offer fits this villager; otherwise it waits or refuses."""
    partner = next(person for person in terms["participants"] if person != aid)
    trust = sim.agents[aid].trust.get(partner, 0.0)
    if trust < 0:
        return "decline"
    if _goal_aligned(sim, aid, partner, terms):
        return "accept"
    from . import reflection
    helper = reflection.choose_helper(sim, sim.agents[aid], terms["task"])
    if trust > 0 and helper is not None and helper.id == partner:
        return "accept"
    return "defer"


def fallback(sim, aid):
    request = context(sim, aid)
    if request is None:
        return
    conversation = request["conversation"]
    move, terms, speech = "say", None, "I am glad we have time to talk."
    if conversation["offer"] and conversation["offered_by"] != aid:
        try:
            checked = validate_terms(sim, conversation, conversation["offer"])
        except ValueError:
            move, terms, speech = "decline", None, "I cannot make that meeting. Let us try another time."
        else:
            choice = acceptance(sim, aid, checked)
            if choice == "accept":
                move, terms, speech = "accept", checked, "Yes, I can make that time. I will meet you there."
            elif choice == "decline":
                move, terms, speech = "decline", None, "I would rather not agree to that. I need a more reliable plan."
            else:
                move, terms, speech = "say", None, "I need to think about that before I agree."
    elif not conversation["transcript"]:
        start = request["earliest_start"] + 30
        terms = {"participants": conversation["participants"], "place": "bench", "task": "talk",
                 "start_minute": start, "duration_minutes": 30}
        try:
            validate_terms(sim, conversation, terms)
            move, speech = "propose", "Would you like to meet at the bench for a quiet talk?"
        except ValueError:
            terms = None
    apply_turn(sim, aid, {"contract": CONTRACT, "conversation_id": request["conversation_id"],
                        "turn": request["turn"], "move": move, "speech": speech, "terms": terms})


def update(sim, *, advance=True):
    for conversation in sim.conversations:
        if conversation["status"] != "active":
            continue
        if not available(sim, conversation):
            finish(sim, conversation, "interrupted", "participants unavailable")
        elif sim.step - conversation["created_step"] >= 8:
            finish(sim, conversation, "expired", "conversation timed out")
        elif any(meeting_action(sim, aid) for aid in conversation["participants"]):
            finish(sim, conversation, "interrupted", "an accepted meeting is due")
        elif advance and (sim.agents[speaker(conversation)].provider is None
                          or sim.step >= sim.agents[speaker(conversation)].provider_until):
            fallback(sim, speaker(conversation))
    for commitment in sim.commitments:
        if commitment["status"] != "scheduled":
            continue
        terms = commitment["terms"]
        people = terms["participants"]
        if (sim.cognition_mode != "hybrid" or sim.weather == "storm" or sim.step < sim.festival_until
                or any(aid not in sim.agents for aid in people)):
            conclude(sim, commitment, "cancelled", "participants unavailable")
        elif sim.total_minutes >= terms["start_minute"] + terms["duration_minutes"]:
            conclude(sim, commitment, "completed" if commitment["attended"] == people else "missed", "meeting time ended")


def conclude(sim, commitment, status, reason):
    from . import reflection
    commitment.update(status=status, reason=reason)
    people = commitment["terms"]["participants"]
    outcome = sim.event(
        f"Meeting to {commitment['terms']['task'].replace('_', ' ')} {status}: {reason}.",
        "commitment", people, task=commitment["terms"]["task"],
    )
    reflection.adjust_trust(sim, commitment, outcome)
    for aid in people:
        sim.request_replan(aid, "commitment_" + status)


def meeting_action(sim, aid):
    for commitment in sim.commitments:
        terms = commitment["terms"]
        if commitment["status"] != "scheduled" or aid not in terms["participants"] or aid not in sim.agents:
            continue
        try:
            departure = terms["start_minute"] - travel(sim, aid, terms["place"]) - 15
        except ValueError:
            departure = sim.total_minutes
        if departure <= sim.total_minutes < terms["start_minute"] + terms["duration_minutes"]:
            return Action(terms["place"], "meeting to " + terms["task"].replace("_", " "))
    return None


def attend(sim):
    for commitment in sim.commitments:
        terms = commitment["terms"]
        if (commitment["status"] == "scheduled"
                and terms["start_minute"] <= sim.total_minutes < terms["start_minute"] + terms["duration_minutes"]
                and all(aid in sim.agents and (sim.agents[aid].x, sim.agents[aid].y) == sim.world.point(terms["place"])
                        for aid in terms["participants"])):
            if not commitment["attended"]:
                commitment["attended"] = list(terms["participants"])
                sim.event(f"Both villagers attended their meeting to {terms['task'].replace('_', ' ')} at {terms['place']}.",
                          "commitment", terms["participants"])
            for aid in terms["participants"]:
                agent = sim.agents[aid]
                if (agent.role in CRAFTS.get(terms["task"], ()) and sim.step - agent.last_work >= 4
                        and sim._result_options(agent, terms["place"])):
                    agent.last_work = sim.step
                    sim._work(agent, terms["place"])


def start_nearby(sim):
    if sim.weather == "storm" or sim.step < sim.festival_until or not 6 <= sim.minute // 60 < 23:
        return
    villagers = list(sim.agents.values())
    for index, first in enumerate(villagers):
        for other in villagers[index + 1:]:
            people = [first.id, other.id]
            if any(active(sim, aid) or meeting_action(sim, aid) for aid in people):
                continue
            if min(sim.step - first.last_chat, sim.step - other.last_chat) < 8:
                continue
            if abs(first.x - other.x) + abs(first.y - other.y) > 2:
                continue
            social = any(goal["id"] == "social" and not goal["completed"] and goal["partner"] == partner.id
                         for agent, partner in ((first, other), (other, first)) for goal in agent.goals)
            remembered = any(m["kind"] == "promise" and other.id in m["agents"]
                             for m in sim.memory_store(first.id).retrieve(other.name, now=sim.total_minutes, limit=3))
            if not social and not remembered:
                continue
            sim.conversations.append({"id": len(sim.conversations) + 1, "participants": people,
                                      "topic": "Find time to help each other and catch up", "status": "active",
                                      "created_step": sim.step, "transcript": [], "offer": None,
                                      "offered_by": None, "reason": None})
            first.last_chat = other.last_chat = sim.step


def validate_state(sim):
    """Reject malformed or unilateral saved agreements before replacing a session."""
    busy, agreements = set(), {}
    for identifier, conversation in enumerate(sim.conversations, 1):
        if (not isinstance(conversation, dict)
                or set(conversation) != {"id", "participants", "topic", "status", "created_step", "transcript", "offer", "offered_by", "reason"}
                or type(conversation["id"]) is not int or conversation["id"] != identifier
                or not isinstance(conversation["participants"], list) or len(conversation["participants"]) != 2
                or any(not isinstance(aid, str) or aid not in sim.residents for aid in conversation["participants"])
                or len(set(conversation["participants"])) != 2
                or conversation["status"] not in ("active", "completed", "interrupted", "expired", "declined")
                or type(conversation["created_step"]) is not int or not 0 <= conversation["created_step"] <= sim.step
                or not isinstance(conversation["topic"], str) or not 1 <= len(conversation["topic"]) <= 200
                or not isinstance(conversation["transcript"], list) or len(conversation["transcript"]) > 6
                or (conversation["reason"] is not None and (not isinstance(conversation["reason"], str) or len(conversation["reason"]) > 200))):
            raise ValueError("Invalid conversation history")
        people = conversation["participants"]
        if conversation["status"] == "active":
            if (busy.intersection(people) or not set(people) <= set(sim.agents)
                    or len(conversation["transcript"]) >= 6 or not available(sim, conversation)
                    or sim.step - conversation["created_step"] >= 8 or conversation["reason"] is not None):
                raise ValueError("Conflicting active conversations")
            busy.update(people)
        rebuilt = {**conversation, "offer": None, "offered_by": None, "transcript": []}
        previous_step = conversation["created_step"]
        terminal = None
        previous_memory = -1
        for index, turn in enumerate(conversation["transcript"]):
            if (terminal or not isinstance(turn, dict)
                    or set(turn) != {"speaker", "step", "move", "speech", "terms", "memory_id"}
                    or turn["speaker"] != people[index % 2] or type(turn["step"]) is not int
                    or not previous_step <= turn["step"] <= sim.step
                    or turn["step"] - conversation["created_step"] >= 8
                    or type(turn["memory_id"]) is not int
                    or turn["memory_id"] <= previous_memory):
                raise ValueError("Invalid conversation transcript")
            for aid in people:
                memory = sim.memory_store(aid).get(turn["memory_id"])
                if (memory is None or memory["kind"] != "conversation" or memory["step"] != turn["step"]
                        or memory["agents"] != people
                        or memory["text"] != f"{sim.residents[turn['speaker']].name}: “{turn['speech']}”"):
                    raise ValueError("Conversation evidence does not match the turn")
            validate_turn({**turn, "contract": CONTRACT, "conversation_id": identifier, "turn": index},
                          {"conversation": rebuilt, "conversation_id": identifier, "turn": index, "speaker": turn["speaker"]})
            previous_step = turn["step"]
            previous_memory = turn["memory_id"]
            if turn["terms"] is not None:
                now = sim.world.config["start_minute"] + turn["step"] * 15
                if not now < turn["terms"]["start_minute"] <= now + 1440:
                    raise ValueError("Invalid historical meeting time")
                task = turn["terms"]["task"]
                if task in CRAFTS and not any(sim.residents[aid].role in CRAFTS[task] for aid in people):
                    raise ValueError("Meeting lacks the appropriate craft")
            if turn["move"] in ("propose", "counter"):
                rebuilt.update(offer=turn["terms"], offered_by=turn["speaker"])
            elif turn["move"] == "accept":
                agreements[identifier] = turn["terms"]
                terminal = "completed"
            elif turn["move"] == "decline":
                terminal = "declined"
            rebuilt["transcript"].append(turn)
        if conversation["offer"] != rebuilt["offer"] or conversation["offered_by"] != rebuilt["offered_by"]:
            raise ValueError("Saved offer differs from the transcript")
        if terminal and conversation["status"] != terminal:
            raise ValueError("Conversation status differs from its final turn")
    used, scheduled = set(), []
    for identifier, commitment in enumerate(sim.commitments, 1):
        if (not isinstance(commitment, dict)
                or set(commitment) != {"id", "conversation_id", "terms", "status", "attended", "reason"}
                or type(commitment["id"]) is not int or commitment["id"] != identifier
                or type(commitment["conversation_id"]) is not int or commitment["conversation_id"] in used
                or commitment["conversation_id"] not in agreements
                or commitment["terms"] != agreements[commitment["conversation_id"]]
                or commitment["status"] not in ("scheduled", "completed", "missed", "cancelled")
                or commitment["attended"] not in ([], commitment["terms"]["participants"])
                or (commitment["status"] == "completed" and not commitment["attended"])
                or (commitment["reason"] is not None and (not isinstance(commitment["reason"], str) or len(commitment["reason"]) > 200))):
            raise ValueError("Invalid or unaccepted commitment")
        used.add(commitment["conversation_id"])
        terms = terms_shape(commitment["terms"], commitment["terms"]["participants"], sim.world.places)
        if commitment["attended"] and sim.total_minutes < terms["start_minute"]:
            raise ValueError("Attendance cannot precede the meeting")
        if commitment["status"] == "scheduled":
            if (sim.cognition_mode != "hybrid" or not set(terms["participants"]) <= set(sim.agents)
                    or sim.total_minutes >= terms["start_minute"] + terms["duration_minutes"]
                    or commitment["reason"] is not None):
                raise ValueError("Invalid scheduled meeting")
            for other in scheduled:
                shared = set(terms["participants"]) & set(other["participants"])
                if not shared:
                    continue
                gap = travel(sim, next(iter(shared)), terms["place"], sim.world.point(other["place"]))
                if not (terms["start_minute"] + terms["duration_minutes"] + gap <= other["start_minute"]
                        or other["start_minute"] + other["duration_minutes"] + gap <= terms["start_minute"]):
                    raise ValueError("Conflicting scheduled meetings")
            scheduled.append(terms)
    if used != set(agreements):
        raise ValueError("Accepted meeting is missing its schedule")
