from __future__ import annotations

import argparse
import json
from pathlib import Path

from .simulation import Simulation
from .persistence import export_session, read_session, restore_session, write_session


def main():
    parser = argparse.ArgumentParser(
        description="An autonomous little fishing village. No API key required."
    )
    parser.add_argument(
        "--headless", action="store_true", help="run without a web server"
    )
    parser.add_argument(
        "--steps", type=int, default=96, help="headless steps (96 = one day)"
    )
    parser.add_argument(
        "--agents", type=int, default=3, choices=range(1, 9), metavar="1–8"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cognition", choices=("rules", "hybrid"), default="rules",
                        help="cognition for a new village (saved sessions retain their mode)")
    parser.add_argument("--output", type=Path, help="write a replayable and resumable JSON story")
    parser.add_argument("--state-file", type=Path, help="checkpoint path (live default: .village-sim/session.json)")
    parser.add_argument("--no-persist", action="store_true", help="start fresh and disable automatic disk saves")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--visitors", action="store_true",
                        help="welcome visiting agents (requires the operator token for controls)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--interval",
        type=float,
        default=2,
        help="seconds between live steps (default: 2)",
    )
    args = parser.parse_args()
    if args.steps < 0 or args.interval <= 0:
        parser.error("steps must be nonnegative and interval must be positive")
    if args.headless:
        state_path = None if args.no_persist else args.state_file
        try:
            sim = restore_session(read_session(state_path)) if state_path and state_path.exists() else Simulation(args.agents, args.seed, cognition_mode=args.cognition)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        snapshots = [sim.snapshot()]
        for _ in range(args.steps):
            snapshots.append(sim.tick())
            if state_path:
                write_session(state_path, export_session(sim))
        if state_path and not args.steps:
            write_session(state_path, export_session(sim))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "world": sim.world.config,
                        "snapshots": snapshots,
                        "session": export_session(sim),
                    },
                    ensure_ascii=False,
                )
            )
        print(
            f"Day {sim.day}, {sim.snapshot()['time']} · {sim.step} steps · {len(sim.agents)} villagers · {len(sim.events)} events"
        )
        for agent in sim.agents.values():
            print(
                f"  {agent.name}: {agent.activity}; {len(agent.memory)} memories, {agent.work_count} tasks completed"
            )
        if args.output:
            print(f"Recording saved to {args.output}")
    else:
        from .server import serve

        state_path = None if args.no_persist else args.state_file or Path(".village-sim/session.json")
        try:
            serve(args.host, args.port, args.agents, args.seed, args.interval, state_path,
                  cognition_mode=args.cognition, visitors_enabled=args.visitors)
        except (OSError, ValueError) as error:
            parser.error(str(error))


if __name__ == "__main__":
    main()
