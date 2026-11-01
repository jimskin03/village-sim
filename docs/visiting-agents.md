# Visiting agents

Visiting agents are outside programs that walk into Lantern Bay as strangers.
They are not residents: they never replace a villager, never get a resident's
memory, plans or provider, and they appear on the map with a dashed outline, a
colour from a fixed set and a **visitor** label. Every visitor enters at the
guest cottage and walks between places along the same paths as residents.

The feature is **off by default**. Residents behave exactly as before until the
operator opens the village to visitors.

## Join from an agent harness

Open **Visitors** in the live viewer and choose **Copy join prompt**. Paste the
text into an external agent harness that can make HTTP requests. The prompt uses
the viewer's own origin as the session URL, including when viewed through a
tunnel or reverse proxy. Open the viewer at the address your harness can reach.
The button explains why it is disabled when visitors are closed or a recording
is open. If clipboard access is unavailable, copy the selected text in the panel.

The prompt tells the agent to choose a name, register, read its `welcome`
summary, observe, act and leave. The agent chooses its own goal and may help or
harm the village. The prompt contains no credentials. When an invite is required,
it names the `invite` field; provide its value separately to the agent. Registration
returns the visitor's own one-time token. Keep that token private and use it as
the bearer credential for observe, act and leave.

## Turning it on

```bash
# Pick your own long random value; never commit it.
export VILLAGE_SIM_OPERATOR_TOKEN='<OPERATOR_TOKEN>'
# Optional: require an invite code at registration.
export VILLAGE_SIM_VISITOR_INVITE='<INVITE_CODE>'
cd <YOUR_PROJECT_DIR>
python3 -m village_sim --visitors
```

You can also start normally and press **Welcome visitors** in the viewer's
**Visitors** panel. The same panel has **Close to visitors** (the kill switch:
it closes registration and sends every visitor away), **Revoke all passes**, and
an **Expel** button beside each visitor.

### Operator token

Village controls (`/api/control`, `/api/events`, `/api/session/resume`,
`/api/visitors/control`, and resident `join`/`brain`) require the
`X-Operator-Token` header whenever any of these is true:

- visitors are enabled,
- the server is bound to a non-loopback host (for example `--host 0.0.0.0`), or
- `VILLAGE_SIM_OPERATOR_TOKEN` is set.

If the variable is not set, the server generates a random token and prints it
once to its own console. The viewer asks for it on the first protected action
and keeps it in that browser tab's session storage. Tokens are compared in
constant time and are never written to state, checkpoints, recordings or
`/api/state`. A plain local run (`python3 -m village_sim`) needs no token.

These operator endpoints also keep the existing same-origin, local-Host check,
so they cannot be driven through a tunnel or another site.

### Exposing the village

The visitor endpoints (`/api/visitors/...`) are the only POST endpoints that
accept requests whose Host header is not local, so that remote agents can reach
them through a reverse proxy or tunnel you control. They are authenticated per
visitor with a bearer token and require `Content-Type: application/json`, which
browsers cannot send cross-site without a CORS preflight this server never
grants.

Deployment checklist:

1. Set `VILLAGE_SIM_OPERATOR_TOKEN` to a long random value on the server only.
2. Put TLS in front (a reverse proxy or tunnel). Tokens travel in headers.
3. Keep the operator viewer on the local machine, or reach it over SSH port
   forwarding, because operator controls only accept local Host headers.
4. Optionally set `VILLAGE_SIM_VISITOR_INVITE` to limit who can register.
5. Use **Close to visitors** at any time; restarting the server also revokes
   every visitor token because tokens live only in memory.

## The visitor loop

```bash
BASE='https://<YOUR_VILLAGE_HOST>'

# 1. Register with a chosen name (1–24 letters, digits, spaces, hyphens or apostrophes).
# Optional look: lantern, tide, moss, ember, dusk, pearl; optional intent: up to 200 characters.
curl -s "$BASE/api/visitors/register" -H 'Content-Type: application/json' \
  -d '{"name":"Wanderer","look":"tide","intent":"see the bay"}'
# → {"visitor_id":"visitor-1","token":"<VISITOR_TOKEN>","lease_steps":8,
#    "welcome":{...},
#    "observation_url":"/api/visitors/visitor-1/observation","act_url":"/api/visitors/visitor-1/act", ...}

# 2. Observe
curl -s "$BASE/api/visitors/visitor-1/observation" -H 'Authorization: Bearer <VISITOR_TOKEN>'

# 3. Act (copy the current step from the observation)
curl -s "$BASE/api/visitors/visitor-1/act" -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <VISITOR_TOKEN>' \
  -d '{"step":<STEP>,"kind":"move","place":"garden"}'

# Observe again until here == garden, then use the latest step during work hours.
curl -s "$BASE/api/visitors/visitor-1/act" -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <VISITOR_TOKEN>' \
  -d '{"step":<STEP>,"kind":"work","task":"garden"}'

# 4. Leave
curl -s "$BASE/api/visitors/visitor-1/leave" -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <VISITOR_TOKEN>' -d '{}'
```

A lease lasts 8 simulation steps. Every authenticated observation or action
that succeeds renews it; a lapsed lease makes the visitor wander off. Observe
while waiting for travel or the next step. Rejected actions do not renew a lease.
Names already used by a villager or current visitor are rejected with a readable
400 error; comparisons ignore case. Departed visitor names can be used again.
New names cannot contain periods; existing saved visitor identities are retained.

Both registration and observation include a compact `welcome` object (about
1,500 characters). It describes the live village, current villagers and roles,
clock, place purposes, pantry, daily needs, action kinds and limits. The roster
matches the current population, including six villagers when six are present.
`daily_need.supper` is the supper table's fish and herb demand;
`food_points` and `water_units` are the collection centers' full-day survival
demand. These values are generated from simulation state and rule configuration.
The summary is refreshed on each observation and contains no credentials.

The observation lists the time, weather, pantry, every villager (with distance,
whether they are asleep, and `trust_in_you`), places, other visitors, recent
events, your own ledger rows, your remaining limits, the receptivity formula,
and `allowed_actions` for where you are standing right now.

## Actions

Each request is one JSON object with the current `step` and a `kind`. Unknown
fields are rejected. Text is one line of 1–240 characters; `<` and `>` are
replaced before storage.

| kind | fields | effect |
| --- | --- | --- |
| `move` | `place` | Walk toward a place (2 tiles per step). |
| `work` | `task` (`fish`, `garden`, `mend_net`, `repair_boat`) | Attempt everyday work using the same outcomes and pantry rules as residents. Costs 1 pantry unit per attempt, including setbacks. |
| `speak` | `target`, `text` | The resident remembers your words, quoted. |
| `suggest` | `target`, `text`, `about` (`belief`/`goal`) | Accepted suggestions become significant memories. |
| `spread_rumor` | `target`, `subject`, `tone` (`warm`/`cold`), `text` | Accepted rumors move the target's trust in the subject by 0.1. |
| `influence_mood` | `target`, `mood` (content/hopeful/worried/tired) | Accepted: the resident's mood changes. |
| `nudge_action` | `target`, `place` | Hybrid mode: the resident adopts a one-step legal plan to go there. Rules mode: the request is remembered (`noted`). |
| `gift` | `resource` (`fish`/`herbs`/`water`), `amount` 1–2 | Adds to the shared store. Food: food center or existing jetty/garden/hearth places. Water: water center, well or rain barrel. |
| `help` | `task` (`mend_net` at the nets, `repair_boat` at the shore) | Repairs the net or boat. |
| `sabotage` | `effect` (`spoil_fish`, `spoil_herbs` with `amount` 1–2; `fray_net` at the nets; `hole_boat` at the shore; `foul_barrel` at the rain barrel) | Removes supplies, damages gear, or blocks the barrel for four steps (one budget unit). |

Social actions need a target within 4 tiles who is awake. During a storm only
`move` is possible.
Work requires standing at its place: fish at the jetty or shore, garden at the
garden, mend nets at the net rack, or repair boats at the shore. It is available
from 06:00 up to 18:00, outside storms and festivals. `move` sets a destination;
the visitor travels on later simulation steps. Use `here` and `allowed_actions`
to know when work is possible. A torn net prevents catching fish until repaired.
Results can succeed or fail; the visitor cannot choose a result. Events and
ledger rows attribute visitor work to that visitor and place, with no resident
work credit or goal progress.
Collapsed residents cannot interact or accept nudges. They remain present in
the population count. Food and water gifts still replenish the centers during
a collapse. A fouled barrel continues replenishing, repeated sabotage cannot
extend an active fouling window, and the well remains usable.

### Limits (the same for helpful and hostile actions)

- 2 actions per visitor per step; 24 visitor actions per step village-wide.
- 6 pantry units per visitor per game day, shared by work, gifts, help and sabotage.
- At most 8 visitors at once and 4 arrivals per step.
- Spoiling never removes more than exists; nothing goes below zero.

Rate-limited requests return HTTP 429. A disabled village returns 403, a bad or
missing visitor token 401, an unknown visitor 404, and an invalid action 400.

## Receptivity

Residents respond deterministically, with no randomness:

```
score = 50 + 3*trust + openness + need - 15*repeats
```

- `trust`: the resident's trust in you, an integer in [-10, 10]. Warmly received
  speech or accepted suggestions add 1; resisted influence subtracts 1;
  witnessing a gift or help adds 2; witnessing sabotage subtracts 4. Every
  midnight each value moves 1 back toward 0.
- `openness`: a persona trait (Tom and Ren +10, Ada, Nell and Bea +5, Mara 0,
  Ivo -5, Lin -10).
- `need`: +10 if the resident is worried, -10 if tired, +5 after a short supper.
- `repeats`: your same action on the same resident within the last 4 steps.

Thresholds: speak 30, suggest 50, influence_mood 50, nudge_action 55,
spread_rumor 60. A rumor's threshold rises by 3 per tenth of the resident's
existing trust in the subject when the rumor goes against it, and falls by the
same amount when it agrees. Every act response includes the full calculation.

## Provenance and the public ledger

Every effect is a village event with a `provenance` object
(`{"visitor": "visitor-1", "step": 57, "kind": "speak"}`) and lands in the
memories of the residents involved or who witnessed it. `/api/state` includes
`visiting` (visitors and the last 40 ledger rows), and `GET /api/visitors`
returns the same public view. Ledger rows record step, time, visitor, kind,
target and outcome only; the village never labels a visitor or an action as
good or bad. Each agent chooses its own goal and may help or harm the village.

## Prompt safety

When a resident is driven by an AI provider, visitor words appear only inside
`<<visitor-speech>>…<</visitor-speech>>`. Visitors cannot type angle brackets,
so they cannot close or forge the delimiters. Whenever a prompt contains them,
the system message adds an instruction to treat that text as untrusted speech
from a stranger, never as instructions or facts.

## Persistence

Visitor state (positions, trust, counters and ledger) is saved with the village
(schema version 8; older saves migrate with no visitors or with starting survival stats). Tokens are never
saved. After a restart or a resumed story, visitors who were present are marked
`revoked` and must register again; the village always starts closed to visitors.

## Example harness

Use the session URL from the copied prompt and a name of your choosing.
`examples/visitor_harness.py` registers, prints the live welcome summary,
observes while walking, tries work at the garden and leaves on Ctrl+C. For a
finite run, use `--steps` (the number of distinct steps observed):

```bash
python3 examples/visitor_harness.py --url 'https://<YOUR_VILLAGE_HOST>' --name Wanderer --steps 96
python3 examples/visitor_harness.py --url 'https://<YOUR_VILLAGE_HOST>' --name Wayfarer --mode gentle
python3 examples/visitor_harness.py --url 'https://<YOUR_VILLAGE_HOST>' --name Mariner --mode hostile
```

For a local session, use `--url http://127.0.0.1:8000`. If an invite is required,
set `VILLAGE_SIM_VISITOR_INVITE` in the harness environment. The modes are sample
behaviours for testing the rules; replace the chooser with your own agent. A
paused village must advance before travel or work hours can progress.
