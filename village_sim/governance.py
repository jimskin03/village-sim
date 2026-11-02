"""A small charter: rations, water access, and harvest limits.

A proposal records its electorate and every vote, including abstentions.
A tie keeps the current rule. Trust moves only from a witnessed or reviewed
refusal, never from a bare accusation or from weather, injury, or inability.
"""

from __future__ import annotations

import copy

DOMAINS = {
    "rations": ("need", "equal"),
    "water": ("open", "contribution"),
    "harvest": ("uncapped", "limited"),
}
CHOICES = ("yes", "no", "abstain")
CAUSES = ("success", "chance", "weather", "injury", "inability", "refusal", "rule")


def _rule(name, limit=None):
    return {"rule": name, "limit": limit, "adopted_step": 0, "review_step": None,
            "author": None, "votes": {}, "proposal_id": None}


def initial():
    return {
        "rules": {
            "rations": _rule("need"),
            "water": _rule("open"),
            "harvest": _rule("uncapped"),
        },
        "proposals": [],
        "next_id": 1,
        "grievances": [],
        "sanctions": [],
        "contributions": {},
    }


def support(agent, domain, rule) -> float:
    fair = agent.traits.get("fairness", 0.5)
    generosity = agent.traits.get("generosity", 0.5)
    risk = agent.traits.get("risk", 0.5)
    patience = agent.traits.get("patience", 0.5)
    if domain == "rations":
        if rule == "need":
            return fair * 0.7 + generosity * 0.3
        return (1 - fair) * 0.7 + risk * 0.3
    if domain == "harvest":
        if rule == "limited":
            return patience * 0.6 + (1 - risk) * 0.4
        return risk * 0.6 + (1 - patience) * 0.4
    if rule == "contribution":
        return fair * 0.5 + patience * 0.5
    return (1 - fair) * 0.5 + risk * 0.5


def stance(agent, proposal) -> str:
    """Each resident compares the proposal with the rule already in force."""
    current_rule = proposal.get("current_rule")
    if current_rule not in DOMAINS.get(proposal.get("domain"), ()):
        return "abstain"
    proposed = support(agent, proposal["domain"], proposal["rule"])
    current = support(agent, proposal["domain"], current_rule)
    if proposed > current:
        return "yes"
    if proposed < current:
        return "no"
    return "abstain"


def _eligible(sim):
    from . import survival
    return [aid for aid, agent in sim.agents.items() if not survival.collapsed(agent)]


def _open(sim, domain):
    return next((item for item in sim.governance["proposals"]
                 if item["domain"] == domain and item["status"] == "open"), None)


def propose(sim, aid, domain, rule, reason):
    if sim.gameplay != "living":
        raise ValueError("Rules are decided in the living season")
    if aid not in sim.agents:
        raise ValueError("Unknown villager")
    if domain not in DOMAINS or rule not in DOMAINS[domain]:
        raise ValueError("Unknown village rule")
    if rule == sim.governance["rules"][domain]["rule"]:
        raise ValueError("That rule is already in force")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200:
        raise ValueError("A proposal needs a reason of 1–200 characters")
    if _open(sim, domain) is not None:
        raise ValueError("That question is already before the village")
    electorate = _eligible(sim)
    if aid not in electorate:
        raise ValueError("A collapsed resident cannot propose a rule")
    charter = sim.governance
    proposal = {
        "id": charter["next_id"], "domain": domain, "rule": rule, "author": aid,
        "reason": reason.strip(), "step": sim.step, "day": sim.day, "status": "open",
        "votes": {}, "electorate": electorate,
        "current_rule": charter["rules"][domain]["rule"],
        "limit": 4 if domain == "harvest" and rule == "limited" else None,
    }
    charter["next_id"] += 1
    charter["proposals"].append(proposal)
    sim.event(f"{sim.agents[aid].name} proposed {rule} {domain}: {proposal['reason']}",
              "governance", electorate, proposal_id=proposal["id"])
    vote(sim, aid, proposal["id"], "yes")
    return proposal


def vote(sim, aid, proposal_id, choice):
    if choice not in CHOICES:
        raise ValueError("Vote yes, no, or abstain")
    proposal = next((item for item in sim.governance["proposals"] if item["id"] == proposal_id), None)
    if proposal is None or proposal["status"] != "open":
        raise ValueError("That proposal is not open")
    if aid not in proposal["electorate"] or aid not in sim.agents:
        raise ValueError("This resident is outside the electorate")
    proposal["votes"][aid] = choice
    if set(proposal["electorate"]) <= set(proposal["votes"]):
        _resolve(sim, proposal)
    return proposal


def _resolve(sim, proposal):
    yes = sum(choice == "yes" for choice in proposal["votes"].values())
    adopted = yes * 2 > len(proposal["electorate"])
    proposal["status"] = "adopted" if adopted else "rejected"
    rule = sim.governance["rules"][proposal["domain"]]
    if adopted:
        rule.update(rule=proposal["rule"], limit=proposal.get("limit"),
                    adopted_step=sim.step, review_step=sim.step + 288,
                    author=proposal["author"], votes=dict(proposal["votes"]),
                    proposal_id=proposal["id"])
        text = (f"The village adopted {proposal['rule']} {proposal['domain']} "
                f"by {yes} of {len(proposal['electorate'])}.")
    else:
        text = (f"The village kept {rule['rule']} {proposal['domain']}; "
                f"the proposal for {proposal['rule']} did not reach a majority.")
    sim.event(text, "governance", proposal["electorate"], proposal_id=proposal["id"],
              adopted=adopted)
    return adopted


def close_polls(sim):
    """Cast any remaining votes from personal values, then count."""
    if sim.gameplay != "living":
        return
    for proposal in sim.governance["proposals"]:
        if proposal["status"] != "open":
            continue
        for aid in proposal["electorate"]:
            if aid in proposal["votes"]:
                continue
            agent = sim.agents.get(aid)
            choice = "abstain" if agent is None else stance(agent, proposal)
            proposal["votes"][aid] = choice
        _resolve(sim, proposal)


def consider_shortage(sim, supper):
    """A short table puts one concrete rule in front of the people who are here."""
    if sim.gameplay != "living":
        return None
    if not (supper["missing"]["fish"] or supper["missing"]["herbs"]):
        return None
    # The opening evening is short before anyone has worked. That is not a harvest failure.
    if not any(row["day"] == supper["day"] for row in sim.daily_work.values()):
        return None
    if sim.governance["rules"]["harvest"]["rule"] != "limited" and _open(sim, "harvest") is None:
        electorate = _eligible(sim)
        if not electorate:
            return None
        author = max(electorate, key=lambda aid: (support(sim.agents[aid], "harvest", "limited"), aid))
        return propose(sim, author, "harvest", "limited",
                       "Yesterday's table was short. A few days of lighter harvest may let the bay recover.")
    if sim.governance["rules"]["rations"]["rule"] == "need" or _open(sim, "rations") is not None:
        return None
    electorate = _eligible(sim)
    if not electorate:
        return None
    author = max(electorate, key=lambda aid: (support(sim.agents[aid], "rations", "need"), aid))
    if support(sim.agents[author], "rations", "need") <= support(sim.agents[author], "rations", "equal"):
        return None
    return propose(sim, author, "rations", "need",
                   "The hungriest neighbors should eat before the plates are even.")


def note_contribution(sim, aid, resource, quantity):
    if sim.gameplay != "living" or quantity <= 0:
        return
    row = sim.governance["contributions"].setdefault(aid, {"fish": 0, "herbs": 0, "water": 0})
    row[resource] = row.get(resource, 0) + quantity


def collect_cap(sim, agent) -> int:
    from . import survival
    if sim.gameplay != "living" or sim.governance["rules"]["water"]["rule"] != "contribution":
        return survival.COLLECT_UNITS
    scores = [sim.governance["contributions"].get(aid, {}).get("water", 0) for aid in sim.agents]
    mine = sim.governance["contributions"].get(agent.id, {}).get("water", 0)
    if scores and max(scores) > mine:
        return 2
    return survival.COLLECT_UNITS


def ration_order(sim, diners):
    if sim.gameplay == "living" and sim.governance["rules"]["rations"]["rule"] == "equal":
        return sorted(diners, key=lambda agent: agent.id, reverse=True)
    return sorted(diners, key=lambda agent: (agent.food, agent.id))


def laboring(sim, agent) -> bool:
    if sim.gameplay != "living":
        return False
    return any(item["accused"] == agent.id and item["until"] > sim.step and item["kind"] == "labor"
               for item in sim.governance["sanctions"])


def _event(sim, event_id):
    return next((item for item in sim.events if item["id"] == event_id), None)


def accuse(sim, accuser, accused, claim, evidence_ids):
    """Record a grievance. Trust falls only for a witnessed or reviewed refusal."""
    if sim.gameplay != "living":
        raise ValueError("Grievances are part of the living season")
    if accuser not in sim.residents or accused not in sim.residents or accuser == accused:
        raise ValueError("Unknown villager")
    if not isinstance(claim, str) or not 1 <= len(claim.strip()) <= 200:
        raise ValueError("A grievance needs a claim of 1–200 characters")
    if not isinstance(evidence_ids, list):
        raise ValueError("Evidence must be a list of event ids")
    known = {memory["id"] for memory in sim.residents[accuser].memory}
    causes = []
    witnessed = False
    supported = True
    if not evidence_ids:
        supported = False
    for event_id in evidence_ids:
        event = _event(sim, event_id)
        if event is None or event_id not in known or event.get("cause") not in CAUSES:
            supported = False
            break
        causes.append(event["cause"])
        if (accuser in event.get("agents", []) or accuser in event.get("witnesses", [])
                or event.get("reviewed") is True):
            witnessed = True
    trust_changed = False
    before = sim.residents[accuser].trust.get(accused, 0.0)
    if supported and witnessed and causes and all(cause == "refusal" for cause in causes):
        from .cognition.reflection import bump_trust
        updated = bump_trust(before, -0.1)
        if updated != before:
            sim.residents[accuser].trust[accused] = updated
            trust_changed = True
            sim.event(
                f"{sim.residents[accuser].name}'s trust in {sim.residents[accused].name} "
                f"fell to {updated:.1f} after a reviewed refusal.",
                "trust", [accuser], source_ids=list(evidence_ids), partner=accused,
                change="down", cause="refusal",
            )
    prior = [item for item in sim.governance["grievances"]
             if item["accused"] == accused and item["supported"] and item["causes"] == ["refusal"]]
    sanction = None
    if supported and causes == ["refusal"]:
        sanction = "labor" if prior else "explain"
        if sanction == "labor":
            sim.governance["sanctions"].append({
                "accused": accused, "until": sim.step + 16, "kind": "labor",
                "evidence": list(evidence_ids),
            })
    grievance = {
        "id": len(sim.governance["grievances"]) + 1,
        "accuser": accuser, "accused": accused, "claim": claim.strip(),
        "evidence": list(evidence_ids), "causes": causes, "supported": supported and bool(causes),
        "step": sim.step, "day": sim.day, "trust_changed": trust_changed, "sanction": sanction,
    }
    sim.governance["grievances"].append(grievance)
    cause_text = ", ".join(causes) if causes else "no supporting record"
    sim.event(
        f"{sim.residents[accuser].name} raised a grievance against {sim.residents[accused].name}: "
        f"{grievance['claim']} ({cause_text}).",
        "grievance", [accuser], source_ids=[item for item in evidence_ids if type(item) is int],
        cause=causes[0] if len(causes) == 1 else None,
    )
    return grievance


def validate(charter, personas):
    fresh = initial()
    if not isinstance(charter, dict) or set(charter) != set(fresh):
        raise ValueError("Invalid charter")
    if type(charter["next_id"]) is not int or charter["next_id"] < 1:
        raise ValueError("Invalid charter")
    rules = charter["rules"]
    if not isinstance(rules, dict) or set(rules) != set(DOMAINS):
        raise ValueError("Invalid charter rules")
    for domain, rule in rules.items():
        if not isinstance(rule, dict) or set(rule) != set(fresh["rules"]["rations"]):
            raise ValueError("Invalid charter rules")
        if rule["rule"] not in DOMAINS[domain]:
            raise ValueError("Invalid charter rules")
        limit = rule["limit"]
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 24):
            raise ValueError("Invalid harvest limit")
        if type(rule["adopted_step"]) is not int or rule["adopted_step"] < 0:
            raise ValueError("Invalid rule history")
        if rule["review_step"] is not None and (type(rule["review_step"]) is not int or rule["review_step"] < 0):
            raise ValueError("Invalid review date")
        if rule["author"] is not None and rule["author"] not in personas:
            raise ValueError("Invalid rule author")
        if rule["proposal_id"] is not None and type(rule["proposal_id"]) is not int:
            raise ValueError("Invalid rule history")
        if not isinstance(rule["votes"], dict):
            raise ValueError("Invalid rule votes")
        for voter, choice in rule["votes"].items():
            if voter not in personas or choice not in CHOICES:
                raise ValueError("Invalid rule votes")
    if not isinstance(charter["proposals"], list):
        raise ValueError("Invalid proposals")
    seen = set()
    for proposal in charter["proposals"]:
        if not isinstance(proposal, dict) or proposal.get("domain") not in DOMAINS:
            raise ValueError("Invalid proposals")
        if proposal.get("rule") not in DOMAINS[proposal["domain"]] or proposal.get("status") not in ("open", "adopted", "rejected"):
            raise ValueError("Invalid proposals")
        if proposal.get("current_rule") not in DOMAINS[proposal["domain"]]:
            raise ValueError("Invalid proposals")
        if (proposal.get("author") not in personas or type(proposal.get("id")) is not int
                or proposal["id"] in seen or proposal["id"] >= charter["next_id"]
                or not isinstance(proposal.get("reason"), str) or len(proposal["reason"]) > 200
                or not isinstance(proposal.get("electorate"), list)
                or not isinstance(proposal.get("votes"), dict)):
            raise ValueError("Invalid proposals")
        seen.add(proposal["id"])
        for voter, choice in proposal["votes"].items():
            if voter not in proposal["electorate"] or choice not in CHOICES:
                raise ValueError("Invalid proposal votes")
        if any(voter not in personas for voter in proposal["electorate"]):
            raise ValueError("Invalid electorate")
    contributions = charter["contributions"]
    if not isinstance(contributions, dict):
        raise ValueError("Invalid contributions")
    for aid, row in contributions.items():
        if aid not in personas or not isinstance(row, dict) or set(row) != {"fish", "herbs", "water"}:
            raise ValueError("Invalid contributions")
        if any(type(row[key]) is not int or row[key] < 0 for key in row):
            raise ValueError("Invalid contributions")
    if not isinstance(charter["grievances"], list) or not isinstance(charter["sanctions"], list):
        raise ValueError("Invalid grievances")
    for grievance in charter["grievances"]:
        if (not isinstance(grievance, dict)
                or grievance.get("accuser") not in personas or grievance.get("accused") not in personas
                or not isinstance(grievance.get("claim"), str)
                or not isinstance(grievance.get("evidence"), list)
                or not isinstance(grievance.get("causes"), list)
                or any(cause not in CAUSES for cause in grievance["causes"])
                or type(grievance.get("supported")) is not bool
                or type(grievance.get("trust_changed")) is not bool
                or grievance.get("sanction") not in (None, "explain", "labor")):
            raise ValueError("Invalid grievances")
    for sanction in charter["sanctions"]:
        if (not isinstance(sanction, dict) or sanction.get("accused") not in personas
                or sanction.get("kind") != "labor" or type(sanction.get("until")) is not int
                or not isinstance(sanction.get("evidence"), list)):
            raise ValueError("Invalid sanctions")


def public_view(sim):
    return copy.deepcopy(sim.governance)
