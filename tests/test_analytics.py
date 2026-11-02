"""Session analytics retains occurrences and pages through the full ledger."""

import unittest

from village_sim.analytics import session_analytics
from village_sim.persistence import export_session, restore_session
from village_sim.simulation import Simulation


class AnalyticsTests(unittest.TestCase):
    def test_complete_history_and_occurrence_counts_survive_checkpoint(self):
        sim = Simulation()
        for index in range(40):
            sim.event(f"Mara hauled fish {index}.", "work", ["mara"])
        before = session_analytics(sim)
        restored = restore_session(export_session(sim))
        after = session_analytics(restored)
        self.assertEqual(after["event_count"], before["event_count"])
        self.assertEqual(after["by_kind"], before["by_kind"])
        self.assertGreater(before["event_count"], 40)
        cursor = session_analytics(sim, limit=17)["next_before"]
        page = session_analytics(restored, before=cursor, limit=17)
        self.assertEqual([event["id"] for event in page["events"]],
                         [event["id"] for event in session_analytics(sim, before=cursor, limit=17)["events"]])
        self.assertEqual(len(page["events"]), 17)

    def test_departed_villagers_and_unknown_event_kinds_remain_in_session(self):
        sim = Simulation(8)
        sim.set_population(3)
        sim.event("A bell rang from somewhere offshore.", "future_kind", ["mara"])
        data = session_analytics(sim)
        self.assertEqual({person["id"] for person in data["residents"]},
                         {person.id for person in sim.residents.values()})
        self.assertEqual(len(data["residents"]), 8)
        away = next(person for person in data["residents"] if person["id"] == "bea")
        self.assertFalse(away["active"])
        self.assertEqual(data["by_kind"]["future_kind"], 1)
        self.assertIn("future_kind", data["branches"][-1]["kinds"])

    def test_filters_intersect_and_cursors_remain_stable_after_new_events(self):
        sim = Simulation()
        for index in range(6):
            sim.event(f"Mara caught fish {index}.", "work", ["mara"])
        sim.event("Tom mended the boat.", "work", ["tom"])
        first = session_analytics(sim, agent="mara", kinds=["work"], search="FISH", limit=3)
        second = session_analytics(sim, agent="mara", kinds=["work"], search="fish", limit=3)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(first["matching_events"], 6)
        self.assertEqual(len(first["events"]), 3)
        self.assertTrue(all("mara" in event["agents"] for event in first["events"]))
        self.assertEqual(min(event["id"] for event in first["events"]), first["next_before"])
        self.assertTrue(all("tom" in event["agents"] or "Tom" in event["text"]
                            for event in session_analytics(sim, search="tom")["events"]))
        self.assertEqual(session_analytics(sim, search="boat")["matching_events"], 1)
        self.assertEqual(session_analytics(sim, agent="missing")["matching_events"], 0)
        self.assertEqual(session_analytics(sim, kinds=["future_kind"])["events"], [])
        cursor = first["next_before"]
        page = session_analytics(sim, agent="mara", kinds=["work"], search="fish", before=cursor, limit=3)
        sim.event("Mara caught fish again.", "work", ["mara"])
        self.assertEqual(
            session_analytics(sim, agent="mara", kinds=["work"], search="fish", before=cursor, limit=3)["events"],
            page["events"],
        )
        self.assertEqual(session_analytics(sim, agent="mara", kinds=["work"], search="fish")["matching_events"], 7)
