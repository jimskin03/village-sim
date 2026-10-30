# Village Sim v1 technical contract

Implemented by `village_sim`, using Python 3.11+ and a buildless browser viewer.
The original design's remaining choices are resolved for this first demo: Python
core, observer plus world events, and exportable viewer replay. No Reverie source
code is copied.

## World

`village_sim/data/world.json` defines one 40×23 grid, 32 pixels per tile over the
1280×720 original image. Grid coordinates are integers. A tile is walkable when
its grid point falls inside a walkable polygon and outside all inclusive blocked
rectangles. This is a hand-authored navigation mask, not image interpretation.
The last grid row may extend beyond the image; all named locations are in view.

Named places have `id`, `name`, `x`, `y`, and `kind`. Every named place is reachable
from every other named place; tests enforce this. A* uses four cardinal neighbors.
Agents travel at most three tiles per game step. Agents may share tiles. The
viewer places their feet at the center of their tile and interpolates movement.

## Clock and session

A new world begins at 18:00 on day 1, with three villagers by default. One step is
15 game minutes, independent of provider latency. Ninety-six steps advance exactly
24 hours. A live session ticks every two real seconds by default; the viewer offers
½×, 1×, 2× and 4× speed. Pausing stops the clock; single-step advances once while
remaining paused. Every viewer controls the same local server session.

Population ranges from 1–8. Removed residents are kept in session state and retain
their persona, relationships, work history and memory if returned. Their provider
is detached when they leave. Reset starts a new world with the current population
and original seed, retains speed and paused status, revokes provider tokens, clears UI model connections, and
clears recording history. A server restart starts fresh.

## Decisions and memory

The sequence per tick is: advance time, update world events, perceive nearby
villagers, retrieve relevant memories, choose an optional external action or a
rule-based routine, reflect on a new day, execute movement/work, then converse.
The rule-based daily schedule is refreshed once per day. A decision function can
also be injected into `Simulation(decision=...)` for in-process experiments.

Memories are append-only session records. Retrieval prioritizes conversations and
reflections, then recency, returning up to twelve records without deleting older
ones. All memories are retained in snapshots. Conversations increase symmetric
relationship counts with an eight-step cooldown. Reflection records a daily
summary. Rule-based reflection uses no embeddings or model calls.

External actions are a one-step override, not a new permanent routine. They name
a destination, describe an activity, and may contain speech. Missing, late or
invalid external input leaves rules in control. A storm overrides other plans
for twelve steps; a festival draws villagers to the hearth for sixteen. Storms
take priority over festivals. Quiet evening clears both events.

## Snapshot v1

`Simulation.snapshot()` returns a new JSON-compatible object. Each agent includes
its full persona, plan, memory and relationships. Recent global events are capped
at 80 in each snapshot; the full event history and all memories remain in the
session. Each event includes step, day and formatted time.

```json
{
  "schema_version": 1,
  "village": "Lantern Bay",
  "seed": 7,
  "step": 42,
  "day": 2,
  "minute": 270,
  "time": "04:30",
  "weather": "clear",
  "festival": false,
  "agents": [],
  "events": [],
  "event_count": 32
}
```

Agent fields: `id`, `name`, `role`, `home`, `color`, `persona`, `x`, `y`, `activity`,
`destination`, `speech`, `memory`, `plan`, `relationships`, `provider`,
`provider_until`, `last_chat`, `last_work`, `work_count`.

The HTTP session adds public `brains` status (model, base URL, call interval,
request/decision counts and errors), `paused`, `interval` (real seconds per step), and `revision`
(an increasing stream version, including paused control changes). Provider bearer
tokens are never included in a snapshot or recording.

## HTTP interface

All writes accept JSON objects with `Content-Type: application/json`. The default
bind address is `127.0.0.1`. Browser writes must have a matching Host/Origin. CLI
clients may omit Origin. Controls are shared, trusted local operations, not a
multi-user permission system. Agent decisions additionally require a bearer token.

| Method | Path | Result / body |
| --- | --- | --- |
| GET | `/api/world` | World configuration |
| GET | `/api/state` | Current snapshot plus session fields |
| GET | `/api/stream` | SSE, immediate snapshot and later revisions |
| GET | `/api/recording` | Replay file containing world and snapshots |
| POST | `/api/control` | `{ "action": "pause" / "play" / "step" / "reset" }` |
| POST | `/api/control` | `{ "action": "speed", "interval": 0.5 / 1 / 2 / 4 }` |
| POST | `/api/control` | `{ "action": "population", "count": 1–8 }` |
| POST | `/api/events` | `{ "kind": "storm" / "festival" / "clear" }` |
| POST | `/api/agents/{id}/brain` | Connect/update UI provider, disconnect, or think now |
| POST | `/api/agents/{id}/join` | Attach external harness; see harness guide |
| GET | `/api/agents/{id}/observation` | Authenticated observation |
| POST | `/api/agents/{id}/action` | Authenticated one-step decision |
| POST | `/api/agents/{id}/leave` | Detach; preserve memory/personality |

SSE uses `data: <JSON>\n\n`, with periodic comment heartbeats. The viewer reconnects
automatically and receives the latest complete state. The runner uses a condition
lock; actions and clock updates are atomic. Sending stream data occurs outside
that lock. Model calls run on daemon background threads, at most one in flight per villager.
The clock does not wait for providers. The UI adapter validates each result before
queuing it, renews its intent for the configured 4/8/16/32 steps, and emits speech
only once per decision. Storm/festival priorities still win. Automatic requests
are at least 10 seconds apart, or 30 after an error. Requests time out after 30
seconds. Disconnect/reset/population removal discards any in-flight result.
Provider configuration and keys live outside simulation state; keys are never
sent back to viewers or recordings. Blank keys preserve an existing key only
when updating the same endpoint. HTTP is accepted only for loopback providers;
remote providers use HTTPS. Redirects are rejected to avoid forwarding keys.
Only the UI adapter makes provider calls; the external harness API remains a
separate optional route with one-step decisions.

## Recording

A recording contains `schema_version: 1`, `world`, and a `snapshots` array starting
with step zero. Tick, population, and world-event changes are recorded. Play/pause,
speed and provider attachment alone do not add frames. A headless run produces
exactly `steps + 1` snapshots. Recordings are immutable viewing history; import
does not mutate the live world. The viewer validates structure and accepts files
up to 50 MB. Since each frame includes full memory, long recordings grow faster
than the number of steps. Session memory and recording are intentionally stored
in RAM for this first demo.

## UI model configuration

`POST /api/agents/{id}/brain` accepts:

```json
{
  "action": "connect",
  "base_url": "https://api.openai.com/v1",
  "model": "YOUR_MODEL_ID",
  "api_key": "YOUR_KEY",
  "every_steps": 8
}
```

`{"action":"disconnect"}` removes the connection without clearing memories.
`{"action":"think"}` schedules an immediate new request if none is in flight.
The endpoint returns session state with public status, never the key.
The adapter uses the standard messages/model Chat Completions request contract,
with a JSON instruction and server-side action validation for compatibility.
Reference: [OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
