# Lantern Bay · Village Sim

A small, autonomous 2D fishing village. Villagers work, wander, talk, remember and
follow their own routines while lanterns flicker beside the water. Runs locally
with **Python 3.11+**, a modern browser, and **no dependencies or API key**.

![The village at night](docs/assets/village-night-background.jpg)

## Run

From the project directory:

```bash
python3 -m village_sim
```

Open **http://127.0.0.1:8000**. The village runs while the server is open, including
when no browser is connected. Stop it with Ctrl+C. All viewers share one session.

```bash
python3 -m village_sim --port 8080 --agents 5 --interval 1 --seed 42
```

The viewer works on desktop and phone. Pause, advance a single step, adjust speed,
choose 1–8 villagers in settings, or introduce a passing storm or lantern festival.
Select a villager on the map or a card to inspect their persona, daily plan and
session memories. Toggle place labels or zoom using the map controls.

**Save this story** downloads the session as JSON. **Open a recording** replays it
with play/pause, single-step and a scrubber. The live world keeps running during
replay. A recording is for viewing, not restoring a simulation.

Memory belongs to the world: changing an agent's provider or removing and returning
a villager does not erase it. Restarting the server or choosing **Start again**
starts a fresh session. Export first if you want to keep the story.

## Connect an LLM to a villager

1. Select a villager on the map or their card.
2. In **A closer look**, click **Connect LLM**.
3. Choose OpenAI or a custom/local OpenAI-compatible provider.
4. Enter the API base URL, your provider's model ID, and API key (optional for local servers).
5. Click **Connect LLM**. The panel shows thinking, successful decision count, or a connection error.

For OpenAI, the base URL is `https://api.openai.com/v1`. For other providers,
use their OpenAI-compatible base URL. The server appends `/chat/completions`.
Each villager can use a different provider/model. No model ID is hard-coded.

The model receives the villager's persona, a bounded memory view, relationships,
nearby villagers and named places. It chooses an activity, destination and optional
dialogue. That intent lasts for the selected number of steps. The village clock
keeps advancing while the model thinks; rules handle missing or failed decisions.
Automatic calls are at least ten real seconds apart, with a thirty-second backoff
on errors. **Think now** requests a fresh decision, including while paused.

Keys stay in server memory and are excluded from recordings and browser storage.
Reconnect after restarting/resetting the server. **Disconnect** keeps the
villager's identity and memories. You can also connect an external harness using
the [agent API](docs/agent-harness.md).

## Run without a browser

One step is 15 game minutes; 96 steps make one full day. The initial time is 18:00.

```bash
python3 -m village_sim --headless --steps 96
python3 -m village_sim --headless --steps 192 --agents 8 --output recordings/two-days.json
```

Open the exported recording in the browser viewer. A seed makes rule-based runs
reproducible. Optional installation exposes the same CLI as `village-sim`:

```bash
python3 -m pip install -e .
village-sim --headless
```

## Architecture

- `village_sim/world.py`: JSON world, walkable geometry, A* pathfinding.
- `village_sim/simulation.py`: clock, personas, memory, relationships, work and events.
- `village_sim/decisions.py`: replaceable `decide(agent, observation) -> Action`.
- `village_sim/providers.py`: optional OpenAI-compatible model adapter.
- `village_sim/server.py`: local HTTP server, async planning, SSE and harness API.
- `village_sim/static/`: buildless HTML/CSS/JS viewer over the original background.

The simulation imports no rendering or server code. The viewer consumes serializable
snapshots; it never makes agent decisions. Rules drive behavior immediately.
Villagers can use a provider configured in the UI or an external harness through
the API, without blocking the clock or sharing a server-level model.

See the [technical contract](docs/technical-spec.md) and
[external agent guide](docs/agent-harness.md). The original
[design review](docs/village-sim-design-review.md) is retained as design history.

## Check

```bash
python3 -m unittest discover -s tests -v
```

Tests cover a full unattended day, map connectivity, deterministic execution,
session memory beyond 100 steps, provider changes, external action validation,
weather and festival reactions, HTTP controls, streaming and recording. Model tests
use a local mock HTTP provider and cover actual request formatting, independent
models, invalid replies, slow calls, disconnect races and secret redaction.

This is a local first demo: rule-based dialogue uses a small collection of lines,
art remains a night scene throughout the game day, and session history lives in
RAM until exported. The standard-library development server binds to loopback by
default; it is not a public hosting server.
