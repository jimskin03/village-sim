import copy
import json
import unittest
from unittest.mock import patch

from village_sim.cognition import autobiography
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
        self.assertEqual(mara_context["social_context"][0]["resident"], "Ada")
        self.assertEqual(ada_context["social_context"][0]["resident"], "Mara")

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

        sim = Simulation(3)
        sim.step = 56  # Morning work, with no learned shore or jetty record.
        sim.agents["mara"].activity = "remembering a storm rescue at the shore"
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "shore")
        sim.set_autobiography(False)
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "jetty")


if __name__ == "__main__":
    unittest.main()
