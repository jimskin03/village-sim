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
full persona, daily goals, relationships and session memory, nearby agents, active
villager summaries, the pantry, relevant memories, available named places, and a
`suggested_action` from the rule-based brain. Goals expose their title, target,
progress and completion state so a harness can help the villager pursue them.
Use this context with
any model you choose, then submit the action **with the observed step**:

```bash
curl -s http://127.0.0.1:8000/api/agents/mara/action \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{"step":0,"place":"fire","activity":"sharing stories","mood":"hopeful"}'
```

Replace `step: 0` and the example's place/activity with the current observation.
Copy an entry from `allowed_actions`; providers cannot invent a new activity.
Optional `result`, `mood`, `promise`, and `speech` must be selected from
`result_options`, `mood_options`, `promise_options`, and `speech_options` respectively.
`thought` is optional explanatory text of at most 200 characters. Omit slots or
send null to use the rules. The rule-selected action always remains available.

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

Detaching or changing providers preserves identity, memory, relationships and
plans. The next model receives the same villager, with all the experiences from
this session. Removing a villager through population settings detaches its
provider and revokes its token. Reset revokes every token and starts fresh.

For a runnable standard-library example:

```bash
python3 examples/harness.py --agent mara --provider "Demo harness"
```

The example sends the suggested rule-based action. The UI adapter makes actual
model calls without needing this example. Replace its `plan` function
with your own model call; no provider is privileged or hard-coded by the server.

## Checkpoints and local documentation

The same guide is available through the About dialog at `/docs`.
`GET /api/recording` includes a `session` checkpoint alongside replay frames.
`POST /api/session/resume` with `{"session": checkpoint}` validates the entire
checkpoint before replacing the village, then pauses it. Resume revokes all
provider tokens and connections. Provider keys never enter checkpoints.
Live runs checkpoint to `.village-sim/session.json` after each step/control and
resume automatically on launch. Use `--state-file PATH` or `--no-persist` to
choose a different file or disable persistence.
