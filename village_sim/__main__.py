from __future__ import annotations

import argparse
import json
from pathlib import Path

from .simulation import Simulation


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
    parser.add_argument("--output", type=Path, help="write a replayable JSON recording")
    parser.add_argument("--host", default="127.0.0.1")
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
        sim = Simulation(args.agents, args.seed)
        snapshots = [sim.snapshot()]
        for _ in range(args.steps):
            snapshots.append(sim.tick())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "world": sim.world.config,
                        "snapshots": snapshots,
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

        serve(args.host, args.port, args.agents, args.seed, args.interval)


if __name__ == "__main__":
    main()
