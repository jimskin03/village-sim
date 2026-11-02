# A village that learns, survives, and governs itself

## Recommendation

The next stage should make each day's experiences change what villagers expect
and choose. Keep the simulation authoritative over movement, supplies, work
results, and promises. Give each villager a small persistent decision policy
that can learn from real outcomes, use evidence-backed memory, and bargain over
rules. An optional language model can propose a plan or interpretation within
that policy. The same world and learning loop should run deterministically
without a model connection.

"Better every day" should mean that a villager learns more about the bay, the
other residents, and the effects of their own choices. It should not mean that
every resource stock, relationship, or survival score rises each day. Weather,
injury, depleted fishing grounds, failed agreements, and costly political
choices should still be able to make a later day harder. Track improving
calibration and the ability to adapt, alongside welfare and resource condition.

## What the current build tells us

Lantern Bay already has most of the substrate for this work: durable personal
memories, retrieval, short legal plans, evidence-backed reflection, negotiated
meetings, trust after kept or missed commitments, shared supplies, survival
needs, and a replayable event log. Relevant starting points are
[the simulation loop](../village_sim/simulation.py),
[memory retrieval](../village_sim/cognition/memory.py),
[planning validation](../village_sim/cognition/planning.py),
[reflection and trust](../village_sim/cognition/reflection.py), and
[survival and work](../village_sim/survival.py).

The main gap is that remembered experience seldom changes the local rule
policy. [The default schedule](../village_sim/decisions.py) is regenerated
from the same role-based timetable each morning; [work resolution](../village_sim/work.py)
has fixed options and yields; and water sources in [survival](../village_sim/survival.py)
gain one unit per simulated step without a capacity or ecological cost. All
successful production is placed into common stores and shortage tasks are
centrally assigned. Those rules make supplies readable but leave little reason
to bargain about access, effort, or fairness.

A deterministic headless run of the current rules, seed 7, found that after ten
full simulated days each villager still had the same daily plan as at the
start. The three-person village had 1,866 units of water at its two sources,
no collapses, and no fish in the pantry. The eight-person run also kept the
same plans, accumulated 1,758 source water units, and had no collapses. The
exact stores depend on deterministic work outcomes; the point is that source
water accumulated almost without bound while repeating schedules hid the
ecological and political consequences. This is a one-seed baseline, not proof
of how all runs behave.

Another important boundary: in the existing legacy voice contract, a villager's
requested work result can select a listed outcome. In the future action
contract, a villager or model should choose the attempt and effort; only the
world should determine whether it succeeds and what it produces.

## The individual villager loop

Run this loop when a villager needs to decide, or when new evidence calls for a
replan. Routine movement, hunger and thirst updates, and environmental
resolution should not each incur a model call.

```text
observe what I can see and hear
    ↓
update my beliefs, with source, time, and confidence
    ↓
retrieve relevant events, lessons, promises, and grievances
    ↓
build a short list of legal actions and plans
    ↓
score likely consequences using my needs, values, skills, and commitments
    ↓
choose or ask my optional model for one valid proposal
    ↓
let the simulation execute and resolve the action
    ↓
compare expected and actual outcomes; update estimates and memory
    ↓
carry useful lessons into tomorrow
```

### What each villager knows

Give each villager a small, explicit set of stable traits and changing state:

- **Traits:** risk tolerance, patience, fairness preference, generosity,
  sociability, attachment to home, and willingness to defer to a leader. Traits
  should affect choices without scripting a fixed personality.
- **Needs and stress:** hunger, thirst, health, rest, safety, loneliness, and
  concern about dependants. These already have a first implementation in the
  resident stats; distinguish current need from durable personality.
- **Skills:** task-specific experience and a practical success estimate for
  fishing, gardening, repairs, negotiation, and scouting. Practice may raise a
  skill slowly; fatigue, poor tools, and changing conditions still affect the
  actual result.
- **Beliefs:** dated claims about local stock, weather, other residents, and
  rules. Each carries its evidence source (seen, heard from a named person, or
  inferred), confidence, and last checked time. Keep disagreement and mistaken
  beliefs instead of silently replacing them with the omniscient world state.
- **Social and civic state:** trust by person, reciprocal debts and promises,
  remembered help or harm, perceived fairness, political preferences, and
  unresolved grievances. Separate what happened from how the villager
  interprets it.

Start with `Agent` fields and serialized records so state follows a resident
through leave, rejoin, and checkpoint restore. Reuse the existing memory IDs and
source citations. Add an explicit checkpoint schema migration for each new
durable field.

### How a villager chooses

First generate actions the world can actually execute. Candidates might be
fishing at a legal place, gathering herbs, scouting a resource, mending a net,
sharing a ration, resting, attending a meeting, keeping a promise, or asking the
village to change a rule. Attach a target, location, effort, expected duration,
and relevant evidence. The simulation checks availability, travel, health,
meeting commitments, current law, and scarce tools before execution.

Then score the candidates using readable, bounded terms rather than an LLM's
free-form claim that an action is wise:

```text
utility(action) =
    immediate_need_relief
  + expected_personal_and_family_benefit
  + relationship_and_promise_value
  + future_resource_security
  + fit_with_personal_values_and_current_rules
  + small_information_value_when_safe
  - travel_and_effort
  - injury_and_failure_risk
  - lost_time_or_broken_commitments
  - expected_social_and_political_cost
```

Treat immediate safety and a critical health threshold as constraints, so a
high sociability score cannot send a dehydrated person to a meeting instead of
water. A villager can have both a private objective and a community obligation;
their tradeoff should be inspectable in the plan reason, memory, and later
events. A local scorer is the fallback. An attached model may choose among
legal candidates, propose a goal, explain a tradeoff, or suggest a lesson. It
must not set stock counts, invent observation, select a successful result,
change trust directly, or make a promise on another villager's behalf.

### How a villager learns

After every consequential action, compare the prediction with what actually
happened. Update estimates by task, location, season or weather, and tool using
a small learning rate; keep the number of observations so one lucky catch does
not rewrite a belief. Update skill only from meaningful practice, and track
fatigue and equipment separately. Add a reusable lesson only when it cites
supporting events and includes conditions, such as: “When the east jetty is
calm and the net is sound, my catches have been better than at the exposed
shore.” Let later evidence strengthen, weaken, or retire it.

Useful information can be learned from work, observation, conversation,
meetings, a shortage, or a promise kept or broken. Information shared through
conversation should retain who reported it; hearing a rumor is not the same as
seeing a fouled well. A failed choice must not automatically count as
unwillingness: the boat could be damaged or a helper could have been blocked by
weather. Keep that distinction when revising beliefs and trust.

The LLM research supports this architecture's ingredients. Generative Agents
store experiences, reflect on them, and retrieve them for planning and show
that observation, planning, and reflection each matter to believability.
[Voyager](https://arxiv.org/abs/2305.16291) shows how verified skills and
environment feedback can carry lessons into later decisions without changing
model weights. [Reflexion](https://arxiv.org/abs/2303.11366) uses experience
feedback as language memory. Those studies support feedback-grounded memory,
not a claim that an LLM's prose alone will make people realistic or that each
agent must call an LLM every tick. See [Generative Agents: Interactive
Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442).

## Make politics matter to the pantry

Politics should become visible when interests collide over a real resource or
shared obligation. Arguments about fairness only feel consequential if an
accepted rule changes who receives food, who can draw water, how long a group
may fish, what work counts as contribution, or who investigates a disputed
shortage. The existing village meeting and promise system offer a good starting
point for consent and trust.

### Scarcity should have ecology

Replace unlimited replenishment with small, inspectable ecological stocks:

- Fish populations recover slowly, more slowly after heavy harvest, and vary
  with season and weather. Success chances fall as the stock is depleted.
- Gardens need seed, water, soil recovery, and work. Harvest can be abundant
  after patient maintenance and fail after neglect or drought.
- Freshwater has a well level or aquifer, rain capture capacity, and rainfall.
  Extraction, capacity, and regeneration are visible to local observation.
- Nets, boats, tools, fuel, seed, and medicines degrade or require shared labor.
- Food can spoil. Residents can store private food, contribute it to the
  commons, trade, give it away, or hoard it, with a visible provenance trail.

Do not tune the ecology for inevitable collapse. Calibrate it so careful work
and reasonable cooperation sustain the group in ordinary weather, while storms,
losses, overuse, unequal access, or bad policy create difficult but recoverable
stretches. Keep a calm tutorial scenario and adjustable scarcity settings. Use
reproducible seeds to compare policies. Visitors who join the settlement
should consume supplies if they are permanent residents; if they are temporary
outsiders, represent them explicitly as aid workers or guests whose provisions
come from outside, and make that exception part of the world rules.

Bounded regeneration and lasting injury consequences would intentionally change
the current survival rules. Existing tests assert unbounded water-source
replenishment and rapid, nonfatal collapse recovery. Treat scarcity as a new
versioned gameplay model or settings choice, and decide how saved sessions keep
their original rules before changing those assumptions.

### Rules and power

Introduce a compact village charter, not an abstract parliament. Villagers can
propose a rule when a shortage, broken obligation, or repeated dispute makes
one relevant. Example proposals include:

- ration equally, give more to the hungriest, or reserve shares for children,
  injured residents, or caretakers;
- assign fishing and well shifts by rotation, skill, elected steward, or
  contribution record;
- cap daily harvest, protect a nursery patch, save seed, or keep an emergency
  pantry;
- pool harvests, allow private catches, tax some share into a common reserve,
  or trade through a market;
- appoint a steward to maintain the well, oversee stores, call a vote, or
  inspect resource use.

Each proposal has an author, stated reason, affected people, costs, time limit,
and vote or consent record. Residents' values, experience, personal bonds, and
beliefs shape whether they support it. The simulation validates eligibility,
counts the outcome, enforces the accepted rule, records who contributed, and
publishes resulting allocation and grievances. A policy change should change
resource movement or work schedules on the very next applicable action.

Make rule enforcement graduated and accountable: first ask for an explanation
or restitution, then community labor or a temporary restriction after repeated
well-evidenced violations, with a local hearing for disagreement. Preserve the
resident's own belief about the accusation until evidence changes it. Avoid
random crimes or fixed heroes and villains. Theft, hoarding, free riding,
solidarity, and favoritism should be choices with material incentives and
social consequences.

For the first assembly, pick one clear decision rule and persist its electorate,
votes, abstentions, effective date, and review date. A majority of eligible
residents is a reasonable starting point; a tie or failed proposal should keep
the current rule. Later, the village can propose a different process, which
makes the decision rule itself part of politics.

This model follows Ostrom's findings on durable common-pool resource
institutions: boundaries for users and resources, rules suited to local
conditions, participation in rule changes, user monitoring, graduated
sanctions, and accessible local conflict resolution.
[Her Nobel Prize lecture](https://www.nobelprize.org/uploads/2018/06/ostrom_lecture.pdf)
emphasizes that these principles need to fit local ecology and remain flexible;
they are prompts for systems to simulate, not a universal policy template.

### A concrete political story

Suppose several evenings of low catches leave the pantry short. Mara saw the
west shore holding more fish than the jetty and believes a temporary reserve
zone will help. Ren has not seen that catch; he heard from Tom that the boat is
leaking and distrusts a fishing ban proposed by an experienced fisher. Bea is
hungry and argues for need-based supper portions; Lin favors protecting the
garden's last seeds. The villagers exchange evidence at a meeting, negotiate a
three-day harvest limit and a temporary ration rule, and vote. The simulation
enforces the result. Three days later the resource estimate and supper outcomes
show whether the rule helped. Villagers update their policy views, trust, and
memories differently depending on the evidence they witnessed and the burdens
they carried. The next crisis now has a history.

## Implementation roadmap

### Stage 1 — Local knowledge and believable differences

Separate the view a villager receives from the full viewer snapshot. Give each
resident nearby observations, personal work results, explicit announcements,
and claims heard from named people. Store provenance, confidence, and
observation time. Add stable numeric traits to the existing persona, and make
them affect preference weights modestly. Keep rules mode deterministic under a
seed and make uncertainty visible in the UI.

**Done when:** two villagers can hold different reasonable beliefs about one
resource; a report changes the listener's belief with attribution; unseen events
do not appear in private memories; same seed reproduces the same baseline.

### Stage 2 — Outcomes teach tomorrow's policy

Add per-resident skill and expected-yield estimates. Convert work into an
attempt with effort and tools; make the world resolve outcomes. Add a local
candidate scorer with urgent needs, personal traits, estimated yield,
travel/work cost, commitments, and a bounded exploration bonus. Turn end of day
into a private, evidence-citing policy update: what did I expect, what happened,
what would I try tomorrow, and how confident am I? Let a model provide this
update when configured; use a deterministic updater otherwise.

**Done when:** a resident changes task or location after repeated, evidence-backed
failures; a resident's demonstrated skill slightly changes later odds; one
successful event alone has a small effect; LLM delay or failure leaves the
simulation moving; a model cannot choose an outcome.

### Stage 3 — Finite ecology and shared projects

Add ecological stock, regeneration, carrying limits, deterioration, season or
weather effects, spoilage, and long-lived repair projects. Add shared project
obligations such as restoring a boat, maintaining the well, or building storage.
Resolve their effects through the authoritative simulation and keep the
historical state in checkpoints and replays.

**Done when:** overharvesting has visible delayed costs; cooperative maintenance
changes future yield or protection; a calm seed remains sustainable; harsh
seeds produce recoverable tradeoffs instead of unavoidable collapse.

### Stage 4 — Governance with material consequences

Add rule proposal, discussion, support, voting or consensus, adoption,
expiration, monitoring, appeals, and proportionate enforcement. Start with
three concrete domains: supper rations, water access, and harvest scheduling.
Show the current rule and decision evidence beside each pantry allocation and
related journal event.

**Done when:** agent priorities produce different views; accepted rules change
actual allocation or work; residents can remember, contest, and revise rules;
broken rules change trust only when supported by a witness or a reviewed
record; a resolution can earn dissenters' compliance without converting their
beliefs by fiat.

### Stage 5 — LLMs add voice, proposals, and flexible reasoning

Build a new versioned agent action contract around legal action IDs, stated
goal, concise rationale, cited memories, optional speech, and optional lesson
proposal. Invoke models on consequential triggers such as a new shortage,
unfamiliar failure, disputed claim, governance proposal, meaningful relationship
event, or once-daily planning. Revalidate model replies against the current
world, the resident's current evidence, connection identity, and plan revision.
Continue the local policy immediately while a reply is pending, and fall back
to it on any error. Keep model use and token cost per villager visible.

**Done when:** the same agent can use rules, a model, or an external harness;
model actions cannot ignore legal and urgent needs constraints; delayed, invalid,
or missing replies produce a safe deterministic decision; replay is independent
of provider response timing.

### Stage 6 — Make the village legible and evaluate it

Show each resident's needs, destination, short intention, uncertainty, lesson,
and reason for an important choice. Let the observer inspect what each person
knows and how they learned it, the current laws and who supported them, where
supplies came from, and why an allocation was made. Keep an omniscient journal
for the viewer while marking it as distinct from private knowledge.

Evaluate a matrix of seeds, populations, scarcity settings, and provider modes.
Measure resource shortages and recovery, plan diversity, evidence-based
adaptation, surplus, participation and dissent, promise keeping, belief
calibration, and how often a political rule actually changes an outcome. Use
time-series and replay so each change can be compared against the current
baseline. Avoid one "realism" score: different villages can credibly cooperate,
struggle, or fail for different reasons.

**Done when:** a reviewer can trace a visible choice to a need, belief, memory or
rule; a disagreement to different evidence or values; and a changed outcome to
the enacted policy. A seeded run tells a coherent, inspectable story across
weeks while allowing setbacks.

## Architectural fit

Keep the current separation between simulation, provider, and browser. Proposed
ownership:

| Concern | Existing home to extend |
| --- | --- |
| Durable personal memories and cited experiences | `cognition/memory.py` |
| Local observations, beliefs, and provenance | New cognition perception module; feed `Simulation.observe` |
| Legal action generation and scoring | `decisions.py`, then extract into a cognition policy module |
| Work estimates, skills, and world-resolved outcomes | `work.py` plus resident state |
| Ecological stocks, needs, allocation and spoilage | `survival.py`, shared stores, and world state |
| Reflection, trust and future lesson use | `cognition/reflection.py` and memory records |
| Proposals, rules, votes, evidence and enforcement | New `governance.py` module |
| Optional structured model proposals and fallback | `providers.py` and the existing async server coordinator |
| Checkpoint migration and replay | `persistence.py`, event log, and snapshots |
| Intention, knowledge, law, and allocation inspection | Villager details, the journal, and the events panel |

Give each feature meaningful state in the checkpoint schema and include the
same causally relevant events in replay. Store current rules and model lessons
as versioned structured records rather than burying them only in prose. Run
headless sweeps before increasing population or turning on aggressive scarcity.
For every transfer, keep a small ledger entry with resource, quantity, source,
destination, actor, purpose, witnesses when available, and event ID. Allocation,
contribution, and rule compliance should be derived from that ledger so food
cannot appear because a conversation claimed it was shared.

## Reference research

- Park et al., [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442), UIST 2023. Memory stream, reflection, retrieval, and planning for believable agents; the authors also discuss remaining long-term planning and coherence challenges.
- Wang et al., [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://arxiv.org/abs/2305.16291), 2023. Reusable verified skills and environment feedback offer a pattern for carrying successful experience forward without model fine-tuning.
- Shinn et al., [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366), NeurIPS 2023. Task feedback stored as language memory can improve later attempts in its evaluated tasks.
- Ostrom, [Beyond Markets and States: Polycentric Governance of Complex Economic Systems](https://www.nobelprize.org/uploads/2018/06/ostrom_lecture.pdf), Nobel Prize lecture, 2009. Design principles for sustainable common-pool resource institutions and the importance of matching rules to context.
- Zhou et al., [The PIMMUR Principles: Ensuring Validity in Collective Behavior of LLM Societies](https://arxiv.org/abs/2509.18052), 2025 preprint, revised 2026. Its audit and replications motivate testing sensitivity to assumptions and seeds; treat it as methodological guidance rather than established evidence about human realism.
