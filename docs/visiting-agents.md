# Visiting agents

Visiting agents are outside programs that walk into Lantern Bay as strangers.
They are not residents: they never replace a villager, never get a resident's
memory, plans or provider, and they appear on the map with a dashed outline, a
colour from a fixed set and a **visitor** label. Every visitor enters at the
guest cottage and walks between places along the same paths as residents.

The feature is **off by default**. Residents behave exactly as before until the
operator opens the village to visitors.

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

# 1. Register (name: 1–24 letters/digits/space . ' -; look: lantern, tide, moss, ember, dusk, pearl)
curl -s "$BASE/api/visitors/register" -H 'Content-Type: application/json' \
  -d '{"name":"Wanderer","look":"tide","intent":"see the bay"}'
# → {"visitor_id":"visitor-1","token":"<VISITOR_TOKEN>","lease_steps":8,
#    "observation_url":"/api/visitors/visitor-1/observation","act_url":"/api/visitors/visitor-1/act", ...}

# 2. Observe
curl -s "$BASE/api/visitors/visitor-1/observation" -H 'Authorization: Bearer <VISITOR_TOKEN>'

# 3. Act (copy the current step from the observation)
curl -s "$BASE/api/visitors/visitor-1/act" -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <VISITOR_TOKEN>' \
  -d '{"step":<STEP>,"kind":"move","place":"jetty"}'

# 4. Leave
curl -s "$BASE/api/visitors/visitor-1/leave" -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <VISITOR_TOKEN>' -d '{}'
```

A lease lasts 8 simulation steps. Every authenticated observation or action
renews it; a lapsed lease makes the visitor wander off.

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
| `speak` | `target`, `text` | The resident remembers your words, quoted. |
| `suggest` | `target`, `text`, `about` (`belief`/`goal`) | Accepted suggestions become significant memories. |
| `spread_rumor` | `target`, `subject`, `tone` (`warm`/`cold`), `text` | Accepted rumors move the target's trust in the subject by 0.1. |
| `influence_mood` | `target`, `mood` (content/hopeful/worried/tired) | Accepted: the resident's mood changes. |
| `nudge_action` | `target`, `place` | Hybrid mode: the resident adopts a one-step legal plan to go there. Rules mode: the request is remembered (`noted`). |
| `gift` | `resource` (`fish`/`herbs`), `amount` 1–2 | Adds to the pantry. Stand at the jetty or hearth for fish, garden or hearth for herbs. |
| `help` | `task` (`mend_net` at the nets, `repair_boat` at the shore) | Repairs the net or boat. |
| `sabotage` | `effect` (`spoil_fish`, `spoil_herbs` with `amount` 1–2; `fray_net` at the nets; `hole_boat` at the shore) | Removes supplies or damages gear. |

Social actions need a target within 4 tiles who is awake. During a storm only
`move` is possible.

### Limits (the same for helpful and hostile actions)

- 2 actions per visitor per step; 24 visitor actions per step village-wide.
- 6 pantry units per visitor per game day, shared by gifts, help and sabotage.
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
good or bad. Each agent chooses its own alignment.

## Prompt safety

When a resident is driven by an AI provider, visitor words appear only inside
`<<visitor-speech>>…<</visitor-speech>>`. Visitors cannot type angle brackets,
so they cannot close or forge the delimiters. Whenever a prompt contains them,
the system message adds an instruction to treat that text as untrusted speech
from a stranger, never as instructions or facts.

## Persistence

Visitor state (positions, trust, counters and ledger) is saved with the village
(schema version 7; older saves migrate with no visitors). Tokens are never
saved. After a restart or a resumed story, visitors who were present are marked
`revoked` and must register again; the village always starts closed to visitors.

## Example harness

`examples/visitor_harness.py` registers a visitor and runs a sample behaviour:

```bash
python3 examples/visitor_harness.py --url http://127.0.0.1:8000 --mode gentle
python3 examples/visitor_harness.py --url http://127.0.0.1:8000 --mode hostile
```

Both modes are labelled samples for testing the rules; replace `choose()` with
your own agent.
