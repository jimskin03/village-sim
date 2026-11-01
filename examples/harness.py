"""External agent example. Replace plan() with your own model call.

Run the village server first, then `python3 examples/harness.py`.
"""

import argparse
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def plan(observation):
    # Keep the current rule-selected activity. A model may fill result, mood,
    # speech and promise only from the observation's corresponding *_options.
    return {**observation["suggested_action"], "mood": "hopeful"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--agent", default="mara")
    parser.add_argument("--provider", default="Demo harness")
    args = parser.parse_args()
    token = None

    def request(endpoint, body=None):
        headers = {"Content-Type": "application/json"}
        if endpoint == "join" and os.environ.get("VILLAGE_SIM_OPERATOR_TOKEN"):
            # Needed when the village is protected (visitors enabled or a public bind).
            headers["X-Operator-Token"] = os.environ["VILLAGE_SIM_OPERATOR_TOKEN"]
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(
            f"{args.url}/api/agents/{args.agent}/{endpoint}",
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    try:
        token = request("join", {"provider": args.provider})["token"]
        print(f"Attached {args.provider} to {args.agent}. Ctrl+C to detach.")
        last_step = -1
        while True:
            observation = request("observation")
            if observation["step"] != last_step:
                action = plan(observation)
                try:
                    request("action", {**action, "step": observation["step"]})
                    last_step = observation["step"]
                    print(
                        f"Step {last_step}: {action['activity']} at {action['place']}",
                        flush=True,
                    )
                except HTTPError as error:
                    with error:
                        if error.code != 400:
                            raise
                        print("World moved while planning; refreshing observation.")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nDetaching.")
    finally:
        if token:
            try:
                request("leave", {})
            except (HTTPError, OSError):
                pass


if __name__ == "__main__":
    main()
