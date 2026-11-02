import copy
import json
import unittest
from unittest.mock import patch

from village_sim.cognition import autobiography, knowledge, reflection
from village_sim.decisions import Action
from village_sim.persistence import SCHEMA_VERSION, export_session, restore_session
from village_sim.simulation import Simulation


class AutobiographyTests(unittest.TestCase):
    def test_authored_pack_validates_and_has_private_subjective_recall(self):
        mara = autobiography.seed("mara")
        ada = autobiography.seed("ada")
        mara_context = autobiography.recall(mara, "storm rescue Ada at the shore")
        ada_context = autobiography.recall(ada, "storm rescue Mara at the shore")
        self.assertEqual(len(mara["nodes"]), 24)
        self.assertEqual(len(mara["behavior"]["facets"]), 5)
        self.assertTrue(all(item["id"].startswith("mara-") for item in mara_context["memories"]))
        self.assertTrue(all(item["id"].startswith("ada-") for item in ada_context["memories"]))
        self.assertIn("proposed fictional", mara_context["source"])
        self.assertNotEqual(mara_context["memories"][0]["text"], ada_context["memories"][0]["text"])
        self.assertTrue(any("skiff" in item["text"] for item in mara_context["memories"]))
        self.assertTrue(any("skiff" in item["text"] for item in ada_context["memories"]))
        self.assertEqual(mara_context["social_context"][0]["resident"], "Ada")
        self.assertEqual(ada_context["social_context"][0]["resident"], "Mara")
        summary = autobiography.validate_pack()
        self.assertIn("192 memory nodes", summary)
        self.assertIn("40 evidence-linked facets", summary)
        self.assertIn("11 cross-character", summary)

    def test_associative_links_recall_a_scene_the_cue_does_not_name(self):
        mara = autobiography.seed("mara")
        query = "childhood mother father parents"
        linked = {item["id"] for item in autobiography.recall(mara, query)["memories"]}
        bare = copy.deepcopy(mara)
        bare["links"] = []
        direct = {item["id"] for item in autobiography.recall(bare, query)["memories"]}
        self.assertTrue(linked - direct)
        self.assertTrue(all(item.startswith("mara-") for item in linked - direct))

    def test_new_residents_get_separate_append_only_private_histories(self):
        sim = Simulation(3)
        self.assertEqual(set(sim.autobiographies), {"mara", "tom", "lin"})
        mara = sim.observe("mara")
        tom = sim.observe("tom")
        self.assertTrue(all(item["id"].startswith("mara-") for item in mara["life_history"]["memories"]))
        self.assertTrue(all(item["id"].startswith("tom-") for item in tom["life_history"]["memories"]))
        self.assertTrue(all(type(item["id"]) is int for item in mara["relevant_memories"]))
        self.assertNotIn("autobiographies", sim.snapshot())
        self.assertNotIn("life_history", sim.snapshot())
        self.assertFalse(any("mara-m" in event["text"] for event in sim.events))

        runtime_memory = copy.deepcopy(sim.residents["mara"].memory)
        journal = copy.deepcopy(sim.events)
        sim.set_autobiography(False)
        self.assertIsNone(sim.observe("mara")["life_history"])
        self.assertEqual(sim.residents["mara"].memory, runtime_memory)
        self.assertEqual(sim.events, journal)
        sim.set_population(4)
        self.assertNotIn("ada", sim.autobiographies)
        sim.set_autobiography(True)
        self.assertIn("ada", sim.autobiographies)

    def test_checkpoint_round_trip_and_old_save_migration_do_not_reseed(self):
        sim = Simulation(3)
        saved = json.loads(json.dumps(export_session(sim)))
        self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
        restored = restore_session(saved)
        self.assertEqual(restored.autobiographies, sim.autobiographies)

        old = json.loads(json.dumps(saved))
        old["schema_version"] = 10
        old["state"].pop("autobiography_enabled")
        old["state"].pop("autobiographies")
        migrated = restore_session(old)
        self.assertFalse(migrated.autobiography_enabled)
        self.assertEqual(migrated.autobiographies, {})
        self.assertIsNone(migrated.observe("mara")["life_history"])

    def test_malformed_source_or_saved_brain_fails_closed_without_losing_the_village(self):
        with patch("village_sim.simulation.autobiography.seed", side_effect=ValueError("bad source")):
            sim = Simulation(1)
        self.assertFalse(sim.autobiography_enabled)
        self.assertIn("could not be loaded", sim.autobiography_error)
        self.assertEqual(len(sim.events), 2)

        saved = export_session(Simulation(1))
        saved["state"]["autobiographies"]["mara"]["nodes"][0]["fact"] = {"bad": "data"}
        restored = restore_session(saved)
        self.assertFalse(restored.autobiography_enabled)
        self.assertEqual(restored.autobiographies, {})
        self.assertIn("invalid", restored.autobiography_error)
        self.assertEqual(len(restored.agents), 1)

    def test_facet_can_break_a_workplace_tie_and_observed_learning_wins(self):
        mara = autobiography.seed("mara")
        action = Action("jetty", "fishing for the village supper")
        biased = autobiography.bias_work_location(
            mara, "fishing for village supper; clear weather; Old jetty", action, ("jetty", "shore")
        )
        self.assertEqual(biased.place, "shore")
        self.assertEqual(biased.activity, action.activity)
        tom_brain = autobiography.seed("tom")
        tom_brain["behavior"]["facets"][0]["cues"] = ["nets", "inspect"]
        tom = autobiography.bias_work_location(
            tom_brain, "inspect the nets",
            Action("shore", "checking the village boats"), ("shore", "nets"),
        )
        self.assertEqual(tom.place, "nets")
        self.assertEqual(tom.activity, "checking the village boats")

        sim = Simulation(3)
        sim.step = 56  # Morning work, with no learned shore or jetty record.
        mara_agent = sim.agents["mara"]
        mara_agent.activity = "remembering a storm rescue at the shore"
        anchors = [node["fact"] for node in sim.autobiographies["mara"]["nodes"]]
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "shore")
        for _ in range(4):
            sim._work(mara_agent, "shore", "empty_net")
        self.assertEqual(sim.pantry["fish"], 0)
        self.assertEqual(sim.pantry["net_condition"], "sound")
        self.assertEqual([node["fact"] for node in sim.autobiographies["mara"]["nodes"]], anchors)
        learned = sim.observe("mara")
        self.assertEqual(learned["suggested_action"]["place"], "jetty")
        facet = next(item for item in learned["life_history"]["facets"] if item["id"] == "storm_vigilance")
        self.assertTrue(facet["revised"])
        self.assertEqual(facet["previous"], "Treat rapidly changing weather as a serious risk")
        self.assertIn("Fear can overstate", facet["label"])
        self.assertTrue(facet["live_evidence"])
        self.assertTrue(all(type(item) is int for item in facet["live_evidence"]))
        self.assertFalse(any("mara-m" in event["text"] for event in sim.events))
        restored = restore_session(export_session(sim))
        self.assertEqual(restored.autobiographies["mara"]["revisions"], sim.autobiographies["mara"]["revisions"])
        self.assertEqual(restored.observe("mara")["suggested_action"]["place"], "jetty")
        sim.set_autobiography(False)
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "jetty")

    def test_storm_hunger_and_festival_keep_the_rules_and_the_pantry(self):
        sim = Simulation(3)
        sim.step = 56
        mara = sim.agents["mara"]
        mara.activity = "remembering a storm rescue at the shore"
        before = dict(sim.pantry)
        sim.weather = "storm"
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], mara.home)
        sim.weather = "clear"
        sim.festival_until = sim.step + 4
        self.assertNotEqual(sim.observe("mara")["suggested_action"]["place"], "shore")
        sim.festival_until = 0
        mara.food = 44
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "jetty")
        sim.pantry["net_condition"] = "torn"
        mara.food = 100
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "nets")
        self.assertEqual(sim.pantry, {**before, "net_condition": "torn"})
        other = Simulation(3)
        other.set_autobiography(False)
        other.step = 56
        sim.pantry["net_condition"] = "sound"
        sim._work(sim.agents["mara"], "jetty", "silver_fish")
        other._work(other.agents["mara"], "jetty", "silver_fish")
        self.assertEqual(sim.pantry["fish"], other.pantry["fish"])
        self.assertEqual(sim.pantry["fish"], 1)

    def test_hearsay_records_an_attributed_runtime_memory(self):
        sim = Simulation(3, gameplay="living")
        knowledge.remember(
            sim.agents["mara"], subject="fish:jetty", value=2, provenance="observed",
            step=sim.step, day=sim.day, confidence=0.9, event_id=sim.events[0]["id"],
        )
        nodes = copy.deepcopy(sim.autobiographies["lin"]["nodes"])
        event = knowledge.share(sim, sim.agents["mara"], sim.agents["lin"])
        self.assertIn("Mara told Lin", event["text"])
        self.assertEqual(sim.autobiographies["lin"]["nodes"], nodes)
        heard = next(memory for memory in sim.agents["lin"].memory if memory["id"] == event["id"])
        self.assertNotIn("mara-", heard["text"])
        self.assertTrue(all(item["id"].startswith("lin-")
                            for item in sim.observe("lin")["life_history"]["memories"]))
        self.assertFalse(any(str(memory["id"]).startswith("mara-") for memory in sim.agents["lin"].memory))

    def test_new_traits_follow_the_brain_and_import_leaves_trust_alone(self):
        real = autobiography.seed

        def wrapped(aid):
            brain = real(aid)
            if aid == "ada":
                brain["starting_traits"]["risk"] = 0.13
            return brain

        with patch("village_sim.simulation.autobiography.seed", side_effect=wrapped):
            sim = Simulation(3)
            self.assertEqual(sim.agents["mara"].traits["risk"], real("mara")["starting_traits"]["risk"])
            sim.set_population(4)
            self.assertEqual(sim.agents["ada"].traits["risk"], 0.13)
            sim.agents["ada"].traits["risk"] = 0.5
            sim.set_autobiography(False)
            sim.set_autobiography(True)
            self.assertEqual(sim.agents["ada"].traits["risk"], 0.5)
            self.assertEqual(sim.agents["ada"].trust, {})

    def test_tied_trust_asks_the_person_from_the_life_history(self):
        sim = Simulation(8)
        self.assertEqual(sim.agents["lin"].trust, {})
        self.assertEqual(reflection.choose_helper(sim, sim.agents["lin"], "talk").id, "ivo")
        sim.set_autobiography(False)
        self.assertEqual(reflection.choose_helper(sim, sim.agents["lin"], "talk").id, "mara")
        sim.set_autobiography(True)
        sim.agents["lin"].trust["mara"] = 0.4
        self.assertEqual(reflection.choose_helper(sim, sim.agents["lin"], "talk").id, "mara")


if __name__ == "__main__":
    unittest.main()
