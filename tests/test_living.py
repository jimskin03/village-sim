"""Living season: local beliefs, learning, finite stocks, and a small charter."""

import unittest
from pathlib import Path

from village_sim.cognition import knowledge, policy
from village_sim.persistence import SCHEMA_VERSION, export_session, restore_session
from village_sim.simulation import Simulation
from village_sim import ecology, evaluation, governance, survival


def _at(sim, aid, place):
    sim.agents[aid].x, sim.agents[aid].y = sim.world.point(place)


def _force(sim, result):
    sim.rng.choices = lambda population, weights=None, *, cum_weights=None, k=1: [result] * k


class LivingSeasonTests(unittest.TestCase):
    def test_two_residents_keep_different_beliefs_until_they_look_or_are_told(self):
        sim = Simulation(3, 1, gameplay="living")
        mara, tom = sim.agents["mara"], sim.agents["tom"]
        _at(sim, "mara", "jetty")
        _at(sim, "tom", "lin_home")
        sim.ecology["fish"]["jetty"]["stock"] = 3
        knowledge.observe_surroundings(sim, mara)
        sim.ecology["fish"]["jetty"]["stock"] = 20
        knowledge.observe_surroundings(sim, tom)
        self.assertEqual(knowledge.acting_belief(mara, "fish:jetty")["value"], 3)
        self.assertEqual(knowledge.acting_belief(mara, "fish:jetty")["provenance"], "observed")
        self.assertIsNone(knowledge.acting_belief(tom, "fish:jetty"))
        told = knowledge.share(sim, mara, tom)
        self.assertEqual(told["kind"], "report")
        heard = knowledge.acting_belief(tom, "fish:jetty")
        self.assertEqual(heard["value"], 3)
        self.assertEqual(heard["provenance"], "told")
        self.assertEqual(heard["source"], "mara")
        self.assertNotIn(
            "fish:jetty",
            {item["subject"] for item in tom.beliefs if item["provenance"] == "observed"},
        )
        knowledge.observe_surroundings(sim, mara)
        self.assertEqual(knowledge.acting_belief(mara, "fish:jetty")["value"], 20)
        self.assertEqual(knowledge.acting_belief(tom, "fish:jetty")["value"], 3)
        self.assertEqual(sim.snapshot()["ecology"]["fish"]["jetty"]["stock"], 20)
        work = sim.event("Mara hauled an empty net.", "work", ["mara"], cause="chance", place="jetty")
        self.assertNotIn(work["id"], {item["id"] for item in tom.memory})

    def test_one_success_does_not_move_the_goal_and_repeated_failures_do(self):
        lucky = Simulation(1, 3, gameplay="living")
        lucky.step = 64
        mara = lucky.agents["mara"]
        _at(lucky, "mara", "jetty")
        _force(lucky, "silver_fish")
        before = lucky.ecology["fish"]["jetty"]["stock"]
        lucky._work(mara, "jetty")
        self.assertEqual(lucky.ecology["fish"]["jetty"]["stock"], before - 1)
        self.assertEqual(lucky.pantry["fish"], 1)
        self.assertEqual(sum(row["quantity"] for row in lucky.ledger if row["resource"] == "fish"), 1)
        self.assertEqual(mara.estimates["fish:jetty"]["n"], 1)
        self.assertEqual(round(policy.expected_success(mara, "fish", "jetty"), 2), 0.64)
        self.assertEqual(mara.skills["fish"]["level"], 0.22)
        self.assertEqual(mara.lessons, [])
        self.assertIsNone(policy.preferred_work(lucky, mara, "jetty"))
        lucky._refresh_goals(mara)
        self.assertEqual(next(goal["place"] for goal in mara.goals if goal["id"] == "work"), "jetty")
        self.assertEqual(lucky.observe("mara")["suggested_action"]["place"], "jetty")

        practiced = Simulation(1, 3, gameplay="living")
        practiced.step = 64
        mara = practiced.agents["mara"]
        _at(practiced, "mara", "jetty")
        practiced.ecology["fish"]["jetty"]["stock"] = 0
        _force(practiced, "empty_net")
        for _ in range(6):
            practiced._work(mara, "jetty")
        self.assertEqual(mara.estimates["fish:jetty"]["n"], 6)
        self.assertEqual(mara.estimates["fish:jetty"]["successes"], 0)
        self.assertEqual(len(mara.estimates["fish:jetty"]["events"]), 6)
        self.assertEqual(round(policy.expected_success(mara, "fish", "jetty"), 2), 0.22)
        self.assertEqual(mara.skills["fish"]["level"], 0.32)
        self.assertGreaterEqual(mara.skills["fish"]["practice"], 6)
        lesson = next(item for item in mara.lessons if not item["retired"])
        self.assertEqual(lesson["conditions"]["band"], "poor")
        self.assertEqual(lesson["source_ids"], mara.estimates["fish:jetty"]["events"])
        self.assertEqual(policy.preferred_work(practiced, mara, "jetty")["place"], "shore")
        practiced._refresh_goals(mara)
        self.assertEqual(next(goal["place"] for goal in mara.goals if goal["id"] == "work"), "shore")
        self.assertEqual(practiced.observe("mara")["suggested_action"]["place"], "shore")

    def test_skills_and_estimates_survive_leaving_and_a_checkpoint(self):
        sim = Simulation(3, 5, gameplay="living")
        sim.step = 64
        lin = sim.agents["lin"]
        _at(sim, "lin", "garden")
        _force(sim, "first_basil")
        sim._work(lin, "garden")
        practice = lin.skills["garden"]["practice"]
        estimate = dict(lin.estimates["garden:garden"])
        sim.set_population(2)
        self.assertNotIn("lin", sim.agents)
        self.assertEqual(sim.residents["lin"].skills["garden"]["practice"], practice)
        sim.set_population(3)
        self.assertEqual(sim.agents["lin"].skills["garden"]["practice"], practice)
        self.assertIs(sim.agents["lin"], sim.residents["lin"])
        resumed = restore_session(export_session(sim))
        self.assertEqual(resumed.agents["lin"].skills["garden"]["practice"], practice)
        self.assertEqual(resumed.agents["lin"].estimates["garden:garden"], estimate)
        self.assertEqual(resumed.gameplay, "living")
        sim.tick()
        resumed.tick()
        self.assertEqual(sim.ecology, resumed.ecology)
        self.assertEqual(sim.agents["lin"].beliefs, resumed.agents["lin"].beliefs)
        self.assertEqual(sim.ledger, resumed.ledger)
        self.assertEqual(sim.governance, resumed.governance)

    def test_careful_harvest_returns_and_overuse_is_still_short_tomorrow(self):
        careful = Simulation(1, 1, gameplay="living")
        ground = careful.ecology["fish"]["jetty"]
        for _ in range(4):
            ecology.commit(careful, "jetty", "silver_fish", "mara", None)
        ecology.end_day(careful, careful.day)
        self.assertEqual(ground["stock"], ground["capacity"])
        plot = careful.ecology["gardens"]["garden"]
        before = plot["fertility"], plot["seeds"]
        for _ in range(3):
            ecology.commit(careful, "garden", "first_basil", "mara", None)
        ecology.end_day(careful, careful.day)
        self.assertGreaterEqual(plot["fertility"], before[0])
        self.assertGreaterEqual(plot["seeds"], before[1])

        heavy = Simulation(1, 1, gameplay="living")
        self.assertGreater(20, ecology.SUSTAINABLE["jetty"])
        for _ in range(20):
            ecology.commit(heavy, "jetty", "silver_fish", "mara", None)
            heavy.pantry["fish"] += 1
            ecology.transfer(heavy, resource="fish", quantity=1, source="jetty", destination="pantry",
                             actor="mara", purpose="catch")
        self.assertEqual(heavy.ecology["fish"]["jetty"]["stock"], 4)
        self.assertEqual(heavy.pantry["fish"], 20)
        self.assertEqual(sum(row["quantity"] for row in heavy.ledger), 20)
        ecology.end_day(heavy, heavy.day)
        self.assertEqual(heavy.ecology["fish"]["jetty"]["stock"], 4 + ecology.SUSTAINABLE["jetty"] // 2)
        self.assertLess(heavy.ecology["fish"]["jetty"]["stock"], heavy.ecology["fish"]["jetty"]["capacity"])
        garden = heavy.ecology["gardens"]["garden"]
        while garden["seeds"] > 0 and garden["fertility"] >= 12:
            ecology.commit(heavy, "garden", "first_basil", "mara", None)
        self.assertGreater(heavy.ecology["harvested"][f"{heavy.day}:garden"], ecology.SUSTAINABLE["garden"])
        spent = garden["fertility"]
        ecology.end_day(heavy, heavy.day)
        self.assertLess(garden["fertility"], spent)

    def test_boat_repair_is_not_treated_as_fishing(self):
        sim = Simulation(3, 1, gameplay="living")
        sim.ecology["fish"]["shore"]["stock"] = 0
        options, _, cause = ecology.prepare(sim, "shore", ["sealed_hull", "sound_boat", "leaking_boat"])
        self.assertEqual(options, ["sealed_hull", "sound_boat", "leaking_boat"])
        self.assertIsNone(cause)

    def test_values_disagree_and_an_enacted_rule_changes_work_and_supper(self):
        bay = Simulation(3, 7, gameplay="living")
        proposal = governance.propose(
            bay, "lin", "harvest", "limited", "A lighter catch leaves fish for the next morning.")
        self.assertEqual(governance.stance(bay.agents["mara"], proposal), "yes")
        self.assertEqual(governance.stance(bay.agents["tom"], proposal), "no")
        self.assertEqual(governance.stance(bay.agents["lin"], proposal), "yes")
        governance.vote(bay, "mara", proposal["id"], "yes")
        governance.vote(bay, "tom", proposal["id"], "no")
        self.assertEqual(proposal["status"], "adopted")
        self.assertEqual(bay.governance["rules"]["harvest"]["rule"], "limited")
        bay.step = 64
        bay.ecology["harvested"][f"{bay.day}:jetty"] = bay.governance["rules"]["harvest"]["limit"]
        options, _, cause = ecology.prepare(bay, "jetty", ["silver_fish", "mackerel", "empty_net", "torn_mesh"])
        self.assertNotIn("silver_fish", options)
        self.assertEqual(cause, "rule")

        village = Simulation(8, 2, gameplay="living")
        preview = {"domain": "rations", "rule": "equal", "current_rule": "need"}
        self.assertEqual(governance.stance(village.agents["ren"], preview), "yes")
        self.assertEqual(governance.stance(village.agents["bea"], preview), "no")
        self.assertEqual(governance.stance(village.agents["tom"], preview), "yes")
        opposed = [aid for aid, agent in village.agents.items()
                   if governance.stance(agent, preview) == "no"]
        for aid in opposed:
            village.agents[aid].health = 0
            village.agents[aid].collapsed_until = village.step + 4
        ballot = governance.propose(
            village, "ren", "rations", "equal", "Split the crate without weighing who is hungrier.")
        for aid in ballot["electorate"]:
            if aid not in ballot["votes"]:
                governance.vote(village, aid, ballot["id"], governance.stance(village.agents[aid], ballot))
        self.assertEqual(village.governance["rules"]["rations"]["rule"], "equal")
        for aid in opposed:
            village.agents[aid].health = 100
            village.agents[aid].collapsed_until = 0
        for agent in village.agents.values():
            agent.food = 100
        village.agents["mara"].food = 10
        village.agents["tom"].food = 90
        village.pantry.update(fish=1, herbs=0)
        village._supper()
        self.assertEqual(village.suppers[-1]["served"], ["tom"])
        self.assertEqual(village.agents["mara"].food, 10)
        self.assertIn("The equal supper rule decided the portions.", village.suppers[-1]["text"])

        hungry = Simulation(2, 2, gameplay="living")
        hungry.agents["mara"].food = 10
        hungry.agents["tom"].food = 90
        hungry.pantry.update(fish=1, herbs=0)
        hungry._supper()
        self.assertEqual(hungry.suppers[-1]["served"], ["mara"])
        self.assertEqual(hungry.agents["tom"].food, 90)
        self.assertIn("The need supper rule decided the portions.", hungry.suppers[-1]["text"])

        legacy = Simulation(2, 2)
        legacy.pantry.update(fish=1, herbs=0)
        legacy._supper()
        self.assertNotIn("supper rule", legacy.suppers[-1]["text"])
        self.assertEqual(legacy.gameplay, "legacy")

    def test_only_a_witnessed_refusal_moves_trust_and_a_repeat_assigns_labor(self):
        sim = Simulation(3, 4, gameplay="living")
        sim.step = 64
        mara = sim.agents["mara"]
        hidden = sim.event("Tom refused where Mara could not see him.", "work", ["tom"],
                           cause="refusal", witnesses=["lin"], place="nets")
        bare = governance.accuse(sim, "mara", "tom", "I heard he refused the net.", [hidden["id"]])
        self.assertFalse(bare["supported"])
        self.assertFalse(bare["trust_changed"])
        self.assertNotIn("tom", mara.trust)
        for cause in ("weather", "injury", "inability"):
            event = sim.event(f"The catch failed from {cause}.", "work", ["mara", "tom"],
                              cause=cause, witnesses=["mara"], place="jetty")
            grievance = governance.accuse(sim, "mara", "tom", f"Tom let {cause} spoil the catch.", [event["id"]])
            self.assertEqual(grievance["causes"], [cause])
            self.assertFalse(grievance["trust_changed"])
            self.assertNotIn("tom", mara.trust)
        empty = governance.accuse(sim, "mara", "tom", "He just would not help.", [])
        self.assertFalse(empty["trust_changed"])
        first = sim.event("Tom refused to mend the net.", "work", ["mara", "tom"],
                          cause="refusal", witnesses=["mara"], place="nets")
        opened = governance.accuse(sim, "mara", "tom", "He refused the net in front of me.", [first["id"]])
        self.assertTrue(opened["supported"])
        self.assertTrue(opened["trust_changed"])
        self.assertEqual(opened["sanction"], "explain")
        self.assertEqual(mara.trust["tom"], -0.1)
        second = sim.event("Tom refused the net again.", "work", ["mara", "tom"],
                           cause="refusal", witnesses=["mara"], place="nets")
        repeated = governance.accuse(sim, "mara", "tom", "He refused the net a second time.", [second["id"]])
        self.assertEqual(repeated["sanction"], "labor")
        self.assertEqual(mara.trust["tom"], -0.2)
        self.assertTrue(governance.laboring(sim, sim.agents["tom"]))
        action = sim.observe("tom")["suggested_action"]
        self.assertEqual((action["place"], action["activity"]), ("well", "maintaining the well"))

    def test_a_missing_or_invalid_attempt_leaves_the_deterministic_season(self):
        left = Simulation(2, 9, gameplay="living")
        right = Simulation(2, 9, gameplay="living")
        for _ in range(8):
            left.tick()
            right.tick()
        self.assertEqual(export_session(left)["state"]["ecology"], export_session(right)["state"]["ecology"])
        self.assertEqual(left.agents["mara"].beliefs, right.agents["mara"].beliefs)
        self.assertEqual([(a.x, a.y, a.destination) for a in left.agents.values()],
                         [(a.x, a.y, a.destination) for a in right.agents.values()])

        sim = Simulation(1, 9, gameplay="living")
        observation = sim.observe("mara")
        with self.assertRaises(ValueError):
            sim.submit("mara", {"step": sim.step, "contract": policy.ATTEMPT, "place": "well",
                                "activity": "maintaining the well", "result": "silver_fish"})
        with self.assertRaises(ValueError):
            sim.submit("mara", {**observation["suggested_action"], "step": sim.step, "result": "silver_fish"})
        with self.assertRaises(ValueError):
            policy.validate_attempt({"contract": policy.ATTEMPT, "place": "well",
                                     "activity": "maintaining the well", "stock": 1}, observation)
        self.assertNotIn("mara", sim.pending)
        quiet = Simulation(1, 9, gameplay="living")
        quiet.observe("mara")
        sim.tick()
        quiet.tick()
        self.assertEqual(sim.agents["mara"].destination, quiet.agents["mara"].destination)
        self.assertEqual(sim.ecology, quiet.ecology)

        chooser = Simulation(1, 9, gameplay="living")
        chooser.step = 64
        seen = chooser.observe("mara")
        fishing = next(item for item in seen["allowed_actions"] if item["place"] == "jetty")
        chooser.submit("mara", {"step": chooser.step, "contract": policy.ATTEMPT,
                                "place": fishing["place"], "activity": fishing["activity"]})
        _at(chooser, "mara", "jetty")
        _force(chooser, "empty_net")
        chooser.tick()
        self.assertEqual(chooser.agents["mara"].result, "empty_net")
        self.assertIsNone(chooser.pending.get("mara"))

    def test_same_seed_and_schema_nine_checkpoint_round_trip(self):
        first = Simulation(3, 11, gameplay="living")
        second = Simulation(3, 11, gameplay="living")
        for _ in range(6):
            first.tick()
            second.tick()
        self.assertEqual(first.snapshot()["ecology"], second.snapshot()["ecology"])
        self.assertEqual(first.agents["mara"].beliefs, second.agents["mara"].beliefs)
        saved = export_session(first)
        resumed = restore_session(saved)
        first.tick()
        resumed.tick()
        self.assertEqual(first.ecology, resumed.ecology)
        self.assertEqual(first.agents["mara"].estimates, resumed.agents["mara"].estimates)
        self.assertEqual(first.ledger, resumed.ledger)

        legacy = export_session(Simulation(2, 6))
        legacy["schema_version"] = 9
        for resident in legacy["residents"].values():
            for key in ("traits", "beliefs", "skills", "estimates", "lessons"):
                resident.pop(key)
        for key in ("gameplay", "ecology", "governance", "ledger"):
            legacy["state"].pop(key)
        migrated = restore_session(legacy)
        self.assertEqual(migrated.gameplay, "legacy")
        self.assertEqual(migrated.agents["mara"].traits["fairness"], knowledge.TRAITS["mara"]["fairness"])
        self.assertEqual(export_session(migrated)["schema_version"], SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 11)

    def test_legacy_wells_stay_uncapped_until_the_living_season_is_chosen(self):
        sim = Simulation(1, 1)
        for _ in range(20):
            sim.tick()
        self.assertGreater(sim.water_sources["well"]["supply"], 12)
        sim.set_gameplay("living")
        self.assertLessEqual(sim.water_sources["well"]["supply"], sim.ecology["water_capacity"]["well"])
        self.assertLessEqual(sim.water_sources["rain_barrel"]["supply"], sim.ecology["water_capacity"]["rain_barrel"])
        with self.assertRaises(ValueError):
            Simulation(1, scarcity="harsh")
        with self.assertRaises(ValueError):
            sim.set_gameplay("mythic")

    def test_calm_days_stay_sustainable_and_harsh_days_can_recover(self):
        calm = evaluation.run(seed=7, population=3, days=10, scarcity="calm")
        self.assertTrue(calm["within_caps"])
        self.assertEqual(calm["collapses"], 0)
        self.assertGreater(calm["end_health"], 0)
        self.assertGreater(min(calm["beliefs"].values()), 0)
        harsh = evaluation.run(seed=7, population=3, days=10, scarcity="harsh")
        self.assertTrue(harsh["within_caps"])
        self.assertGreater(harsh["shorts"], 0)
        self.assertFalse(harsh["collapsed_now"])
        self.assertGreater(harsh["end_health"], 0)
        self.assertGreaterEqual(harsh["recoveries"], harsh["collapses"])


class LivingPageTests(unittest.TestCase):
    def test_settings_and_story_name_the_season(self):
        root = Path(__file__).resolve().parents[1] / "village_sim" / "static"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        docs = (root / "docs.html").read_text(encoding="utf-8")
        self.assertIn('id="gameplay-mode"', html)
        self.assertIn("WHAT ", script)
        self.assertIn("Village charter", script)
        self.assertIn("A private belief is not a village rule.", script)
        self.assertIn("schema 10", docs)
        self.assertIn('"action":"gameplay","mode":"living"', docs)


if __name__ == "__main__":
    unittest.main()
