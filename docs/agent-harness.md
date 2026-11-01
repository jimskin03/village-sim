# Bring a brain to the village

The world starts without a model provider. A harness can attach to any existing
villager, bring its own provider, read an observation and supply an optional next
action. For this external-harness route, the harness owns model API keys and model calls.
The viewer also offers a built-in **Connect LLM** adapter; see the README. Different
villagers can have different harnesses and providers at the same time.

Start `python3 -m village_sim`, then attach a provider:

```bash
curl -s http://127.0.0.1:8000/api/agents/mara/join \
  -H 'Content-Type: application/json' \
  -d '{"provider":"My local agent"}'
```

The response contains a bearer `token`, `agent_id`, and `observation_url`. Keep the
token in your harness process; it is not part of world state or recordings. An
attached villager cannot be taken over without its current token. Joining again
with that token rotates it. Expired attachments can be replaced.

Read the current observation:

```bash
curl -s http://127.0.0.1:8000/api/agents/mara/observation \
  -H 'Authorization: Bearer YOUR_TOKEN'
```

The observation includes `step`, game time, weather, festival status, the agent's
full persona, daily goals, relationships and a bounded memory view, nearby agents, active
villager summaries, the pantry, relevant memories, available named places, and a
`suggested_action` from the rule-based brain. Goals expose their title, target,
progress and completion state so a harness can help the villager pursue them.
Survival is always on. Observations expose `food`, `water`, `health`,
`villager_count`, `present_villagers` (including the observer), `water_sources`
and `collection_centers`. The centers include their current levels, daily need
and `low` flag. These thresholds update with the present population. All crafts
can fish, garden and collect water; coordinate using the shared roster and
levels. See [survival rules](survival.md).
Use this context with
any model you choose, then submit the action **with the observed step**:

```bash
curl -s http://127.0.0.1:8000/api/agents/mara/action \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{"step":0,"place":"fire","activity":"sharing stories","mood":"hopeful"}'
```

For a focused memory search, add a URL-encoded query to the observation URL:
`/api/agents/mara/observation?memory_query=Tom+boat+promise`. Queries accept up to
500 decoded characters and return at most twelve `relevant_memories` from that
resident. Omit the query to use activity, goals and relationship context.
Memories include `type`, `content`, absolute-minute `timestamp`, `importance`
and supporting `source_ids`, alongside the existing event fields. Retrieval
never removes older memories. See the [memory contract](cognition-spec.md).

Replace `step: 0` and the example's place/activity with the current observation.
In Rules mode, copy the `suggested_action`; providers cannot invent an activity.
Optional `result`, `mood`, `promise`, and `speech` must be selected from
`result_options`, `mood_options`, `promise_options`, and `speech_options` respectively.
`thought` is optional explanatory text of at most 200 characters. Omit slots or
send null to use the rules. The rule-selected action always remains available.

## Autonomous planning

Enable Hybrid in Settings, use `--cognition hybrid` for a new village, or POST
`{"action":"cognition","mode":"hybrid"}` to `/api/control`. Observations then
have `decision_contract: "plan-v1"` and several `allowed_actions`. Copy the
`action`, `location` and `target` of legal entries into a proposal:

```json
{
  "contract": "plan-v1",
  "step": 48,
  "revision": 3,
  "goal": "Visit Tom to help with his boat",
  "steps": [{
    "action": "visit", "location": "shore", "target": "tom",
    "duration_minutes": 45, "reason": "Tom asked for help."
  }],
  "memory_ids": [42]
}
```

Replace step/revision with the observation's `step`/`cognitive_revision` and
choose current legal entries and resident-accessible memory IDs. Submit through
the same authenticated action endpoint. Plans allow 1–8 steps, 15–180 minutes
each in 15-minute increments, with goal/reason text up to 200 characters.
The world checks legality and travel again at execution. Visits confer no work
outputs or agreement by the other resident. Duration counts after arrival.

`cognitive_plan` exposes status/progress; `replan_reason` identifies significant
changes. A stale revision or illegal proposal returns HTTP 400. Legacy voice
slots retain their old authority. Accepted plans persist through checkpoints
and may continue without a provider; absent a valid plan, rules continue.
Model context contains up to twelve relevant and twelve recent memories. Public
resident views show up to 64 recent memories and total `memory_count`; querying
can recall older experiences from the complete checkpoint archive.

Hybrid legal entries also include `drink`, `eat` and `collect_water`. Copy their
locations from `allowed_actions`; seawater is rejected. Personal hunger and
thirst interrupt a plan to meet immediate needs. A resident with a nonzero
`collapsed_until` cannot accept actions and has no legal plan entries until
recovery on the next step.

An accepted action returns `{"accepted":true,"for_step":1}` and is applied on the
next tick if its context still applies. Only one pending action exists per
villager; a newer action replaces it. The core handles walking, material work
outcomes, memory, promises and world-event priorities.

The clock never waits for a model. If planning takes several ticks, fetch a fresh
observation before submitting and reconsider the result against current context.
A stale submission is rejected with HTTP 400; refresh and retry. Without a
pending action, the default brain takes the next step immediately. Attaching a
provider alone does not stop the default routine. Storms and festivals take
priority over submitted decisions.

An attachment lease lasts eight simulation steps. Authenticated observations and
accepted actions renew it. A harness doing long asynchronous planning can keep a
separate heartbeat that reads observations; these reads do not stop the clock.
Expired attachments require a fresh join. Game time stands still while paused,
so leases do too. Polling at most once per step is usually sufficient.

Detach when finished:

```bash
curl -s http://127.0.0.1:8000/api/agents/mara/leave \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN' -d '{}'
```

Detaching preserves identity, memory and relationships, but invalidates an active
model plan and clears pending actions. Rules resume movement on the next tick.
Already accepted meetings remain scheduled; an unfinished conversation is interrupted. The next model receives the same villager, with all the experiences from
this session. Removing a villager through population settings detaches its
provider and revokes its token. Reset revokes every token and starts fresh.

For a runnable standard-library example:

```bash
python3 examples/harness.py --agent mara --provider "Demo harness"
```

The example sends the suggested rule-based action. The UI adapter makes actual
model calls without needing this example. Replace its `plan` function
with your own model call; no provider is privileged or hard-coded by the server.

## Hybrid conversation turns

When it is your resident's turn, observation `conversation` contains a private
context with `conversation_id`, zero-based `turn`, their own memories and schedule,
and the shared transcript. The partner's private memories are excluded. Submit
through `/api/agents/{id}/action` using the current observation `step`:

```json
{
  "contract": "conversation-v1", "step": 48, "conversation_id": 1, "turn": 0,
  "move": "propose", "speech": "Could we repair the boat together?",
  "terms": {"participants": ["mara", "tom"], "place": "shore",
            "task": "repair_boat", "start_minute": 1890, "duration_minutes": 45}
}
```

Copy IDs, participant order and timing from your actual context. Use `say`,
`propose`, `counter`, `accept` or `decline`, with speech of at most 240 characters.
Say/decline have null terms. Accept must repeat the latest partner offer exactly.
The world validates task, place, craft, travel and schedule conflicts before
updating either schedule. Invalid/stale turns return HTTP 400. Successful turns
publish immediately; dialogue is limited to six turns and eight simulation steps.
Hybrid observations also include `trust` (scores from -1 to 1), `preferred_helpers`
by task, and `reflection` when a personal reflection is due. Submit
`reflection-v1` with the current `step`, the reflection context's `evidence_revision`,
and at most three insights. Each insight
needs `text` (1–240 characters), `source_ids` from the supplied evidence,
`participants` who appear in those memories, and `preferred_helper`/`task`
(`null` unless the insight names one cited participant and a listed task).
Unsupported or stale evidence is rejected and does not change trust, biography, or memory.
Trust rises for both people only when they are present together. It falls only
for a person who waited while the other never came.

Observation `commitments` contains your resident's accepted appointments. These
appointments override individual plans when travel is due. Both participants
remember attendance, cancellation or a missed meeting.

## Checkpoints and local documentation

The same guide is available through the About dialog at `/docs`.
`GET /api/recording` streams all archived replay frames and a portable `session`
checkpoint. The server keeps only 256 frames in memory. Persistent replay lives
beside the checkpoint in `session.json.recording.sqlite3`; retain both files when
moving a village. Normal restart preserves the full replay, while reset and
imported-story resume begin a new branch. Archive write failure is visible in
`persistence_error`, and export returns HTTP 503 rather than incomplete history.
`POST /api/session/resume` with `{"session": checkpoint}` validates the entire
checkpoint before replacing the village, then pauses it. Resume revokes all
provider tokens and connections. Provider keys never enter checkpoints.
Live runs checkpoint to `.village-sim/session.json` after each step/control and
resume automatically on launch. Use `--state-file PATH` or `--no-persist` to
choose a different file or disable persistence.

## Protected villages and visitors

When visitors are enabled, the server binds beyond loopback, or
`VILLAGE_SIM_OPERATOR_TOKEN` is set, `join` and `brain` (like the village
controls) also need the `X-Operator-Token` header. `examples/harness.py` sends
it from that environment variable. Outside agents that should arrive as
strangers rather than drive a resident use the separate
[visiting agents API](visiting-agents.md).

