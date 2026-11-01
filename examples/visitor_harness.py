"""Visiting-agent example. Replace choose() with your own agent.

Start the village with visitors enabled first, for example
`python3 -m village_sim --visitors`, then run:

    python3 examples/visitor_harness.py --mode gentle
    python3 examples/visitor_harness.py --mode hostile

Both modes are labelled SAMPLE behaviours for exercising the world rules. The
village itself never labels a visitor as good or bad.
"""

import argparse
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def options(observation, kind):
    return next((a for a in observation["allowed_actions"] if a["kind"] == kind), None)


def nearest(observation):
    awake = [v for v in observation["villagers"] if not v["asleep"]]
    return min(awake, key=lambda v: v["distance"]) if awake else None


def choose_gentle(observation):
    """SAMPLE (gentle): bring herbs to the garden and chat with whoever is near."""
    if (gift := options(observation, "gift")) and observation["limits"]["pantry_units_left_today"]:
        return {"kind": "gift", "resource": gift["resource"][0], "amount": 1}
    if (speak := options(observation, "speak")):
        return {"kind": "speak", "target": speak["target"][0],
                "text": "Lovely evening. Is there anything I can carry for supper?"}
    if observation["here"] != "garden":
        return {"kind": "move", "place": "garden"}
    villager = nearest(observation)
    return {"kind": "move", "place": villager["destination"] or "fire"} if villager else None


def choose_hostile(observation):
    """SAMPLE (hostile): spoil supplies and sow doubt. Bounded by the same limits."""
    if (sabotage := options(observation, "sabotage")) and observation["limits"]["pantry_units_left_today"]:
        effect = sabotage["effect"][0]
        action = {"kind": "sabotage", "effect": effect}
        if effect.startswith("spoil_"):
            resource = "fish" if effect == "spoil_fish" else "herbs"
            if not observation["pantry"][resource]:
                action = None
            else:
                action["amount"] = 1
        if action:
            return action
    if (rumor := options(observation, "spread_rumor")):
        target = rumor["target"][0]
        subject = next((s for s in rumor["subject"] if s != target), None)
        if subject:
            return {"kind": "spread_rumor", "target": target, "subject": subject, "tone": "cold",
                    "text": "I saw them keep the best of the catch for themselves."}
    if observation["here"] != "jetty":
        return {"kind": "move", "place": "jetty"}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--name", default="Wanderer")
    parser.add_argument("--look", default="tide", choices=["lantern", "tide", "moss", "ember", "dusk", "pearl"])
    parser.add_argument("--mode", default="gentle", choices=["gentle", "hostile"])
    parser.add_argument("--intent", default="")
    args = parser.parse_args()
    choose = choose_gentle if args.mode == "gentle" else choose_hostile
    token = None

    def request(path, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(args.url + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    registration = {"name": args.name, "look": args.look}
    if args.intent:
        registration["intent"] = args.intent
    if os.environ.get("VILLAGE_SIM_VISITOR_INVITE"):
        registration["invite"] = os.environ["VILLAGE_SIM_VISITOR_INVITE"]
    reply = request("/api/visitors/register", registration)
    token, vid = reply["token"], reply["visitor_id"]
    print(f"{args.name} arrived as {vid} ({args.mode} sample). Ctrl+C to leave.", flush=True)
    last_step = -1
    try:
        while True:
            observation = request(reply["observation_url"])
            if observation["step"] != last_step:
                last_step = observation["step"]
                action = choose(observation)
                if action:
                    try:
                        result = request(reply["act_url"], {"step": observation["step"], **action})
                        print(f"Step {last_step}: {action['kind']} → {result['outcome']}", flush=True)
                    except HTTPError as error:
                        with error:
                            detail = json.loads(error.read()).get("error", "")
                        if error.code == 401 or error.code == 403:
                            print(f"Pass ended: {detail}")
                            return
                        print(f"Step {last_step}: {action['kind']} not applied ({error.code}: {detail})")
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    try:
        request(f"/api/visitors/{vid}/leave", {})
        print(f"{args.name} left the village.")
    except HTTPError as error:
        error.close()


if __name__ == "__main__":
    main()
