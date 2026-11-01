import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import unittest

import test_server
from village_sim.cognition import conversations as chat
from village_sim.persistence import export_session, restore_session
from village_sim.providers import ProviderConfig
from village_sim.server import Brain, Session
from village_sim.simulation import Simulation


def setup_chat(sim):
    sim.step = 48  # Next morning, with plenty of time for travel and work.
    for agent in sim.agents.values():
        agent.x, agent.y = sim.world.point("shore")
        agent.destination = "shore"
    chat.start_nearby(sim)
    return sim.conversations[-1]


def terms(sim, **changes):
    return {"participants": ["mara", "tom"], "place": "shore", "task": "repair_boat",
            "start_minute": sim.total_minutes + 90, "duration_minutes": 45, **changes}


def turn(sim, aid, move, offer=None, speech="Let us find a time that works for both of us."):
    context = chat.context(sim, aid)
    return {"contract": chat.CONTRACT, "conversation_id": context["conversation_id"],
            "turn": context["turn"], "move": move, "terms": offer, "speech": speech}


def agree(sim):
    offer = terms(sim)
    chat.apply_turn(sim, "mara", turn(sim, "mara", "propose", offer))
    chat.apply_turn(sim, "tom", turn(sim, "tom", "accept", offer))
    return sim.commitments[-1]


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation(2, cognition_mode="hybrid")
        self.conversation = setup_chat(self.sim)

    def test_counter_accept_updates_both_schedules_and_attempts_attendance(self):
        sim = self.sim
        offer = terms(sim)
        chat.apply_turn(sim, "mara", turn(sim, "mara", "propose", offer))
        counter = {**offer, "start_minute": offer["start_minute"] + 30}
        chat.apply_turn(sim, "tom", turn(sim, "tom", "counter", counter))
        self.assertEqual(sim.commitments, [])
        chat.apply_turn(sim, "mara", turn(sim, "mara", "accept", counter))
        self.assertEqual(len(sim.commitments), 1)
        for aid in ("mara", "tom"):
            self.assertEqual(sim.observe(aid)["commitments"][0]["terms"], counter)
            self.assertTrue(any(m["kind"] == "promise" and "agreed" in m["text"] for m in sim.agents[aid].memory))
        restored = restore_session(export_session(sim))
        for _ in range(12):
            self.assertEqual(sim.tick(), restored.tick())
        commitment = sim.commitments[0]
        self.assertEqual(commitment["attended"], ["mara", "tom"])
        self.assertEqual(commitment["status"], "completed")
        self.assertTrue(any(m["kind"] == "commitment" and "attended" in m["text"] for m in sim.agents["mara"].memory))

    def test_words_alone_and_unilateral_or_mismatched_acceptance_do_not_schedule(self):
        sim = self.sim
        chat.apply_turn(sim, "mara", turn(sim, "mara", "say", speech="We have agreed to meet tomorrow."))
        before = export_session(sim)
        with self.assertRaises(ValueError):
            chat.apply_turn(sim, "tom", turn(sim, "tom", "accept", terms(sim)))
        self.assertEqual(export_session(sim), before)
        self.assertFalse(sim.commitments)
        chat.apply_turn(sim, "tom", turn(sim, "tom", "propose", terms(sim)))
        before = export_session(sim)
        with self.assertRaises(ValueError):
            chat.apply_turn(sim, "mara", turn(sim, "mara", "accept", terms(sim, duration_minutes=30)))
        self.assertEqual(export_session(sim), before)

    def test_invalid_terms_overlap_and_unreachable_places_reject_atomically(self):
        for changes in ({"participants": ["mara", "lin"]}, {"task": "fly"}, {"place": "ocean"},
                        {"duration_minutes": True}, {"duration_minutes": 16}, {"start_minute": self.sim.total_minutes},
                        {"start_minute": 1440 + 23 * 60}, {"place": "bench"}):
            with self.subTest(changes=changes):
                before = export_session(self.sim)
                with self.assertRaises(ValueError):
                    chat.apply_turn(self.sim, "mara", turn(self.sim, "mara", "propose", terms(self.sim, **changes)))
                self.assertEqual(export_session(self.sim), before)
        meeting = agree(self.sim)
        with self.assertRaises(ValueError):
            chat.validate_terms(self.sim, self.conversation, meeting["terms"])
        self.sim.agents["mara"].x, self.sim.agents["mara"].y = self.sim.world.point("fire")
        self.sim.world.path = lambda *_: []
        with self.assertRaises(ValueError):
            chat.validate_terms(self.sim, self.conversation, terms(self.sim, start_minute=self.sim.total_minutes + 300))

    def test_each_speaker_has_private_memories_and_shared_transcript_only(self):
        sim = self.sim
        sim.event("Mara secretly treasures a copper compass.", "work", ["mara"])
        sim.event("Tom privately worries about a cracked chisel.", "work", ["tom"])
        mara = json.dumps(chat.context(sim, "mara"))
        self.assertIn("copper compass", mara)
        self.assertNotIn("cracked chisel", mara)
        chat.apply_turn(sim, "mara", turn(sim, "mara", "say", speech="How is your morning?"))
        tom = json.dumps(chat.context(sim, "tom"))
        self.assertIn("cracked chisel", tom)
        self.assertNotIn("copper compass", tom)
        self.assertIn("How is your morning?", tom)

    def test_six_turn_limit_timeout_hazard_and_leaving_range(self):
        for index in range(6):
            aid = ("mara", "tom")[index % 2]
            chat.apply_turn(self.sim, aid, turn(self.sim, aid, "say"))
        self.assertEqual(self.conversation["status"], "completed")
        self.assertIsNone(chat.context(self.sim, "mara"))
        for interruption in ("timeout", "storm", "distance", "departure"):
            sim = Simulation(2, cognition_mode="hybrid")
            conversation = setup_chat(sim)
            if interruption == "timeout":
                sim.step += 8
            elif interruption == "storm":
                sim.inject("storm")
            elif interruption == "distance":
                sim.agents["tom"].x, sim.agents["tom"].y = sim.world.point("fire")
            else:
                sim.set_population(1)
            chat.update(sim)
            self.assertNotEqual(conversation["status"], "active")

    def test_cancellation_and_missed_meetings_record_outcomes(self):
        meeting = agree(self.sim)
        self.sim.inject("storm")
        self.sim.tick()
        self.assertEqual(meeting["status"], "cancelled")
        sim = Simulation(2, cognition_mode="hybrid")
        setup_chat(sim)
        meeting = agree(sim)
        sim.step += 10
        chat.update(sim)
        self.assertEqual(meeting["status"], "missed")
        self.assertTrue(any(m["kind"] == "commitment" for m in sim.agents["tom"].memory))

    def test_short_craft_meeting_performs_world_work_without_giving_helpers_outputs(self):
        sim = self.sim
        offer = terms(sim, start_minute=sim.total_minutes + 15, duration_minutes=15)
        chat.apply_turn(sim, "mara", turn(sim, "mara", "propose", offer))
        chat.apply_turn(sim, "tom", turn(sim, "tom", "accept", offer))
        sim.tick()
        self.assertEqual(sim.commitments[0]["attended"], ["mara", "tom"])
        self.assertEqual(sim.agents["tom"].work_count, 1)
        self.assertEqual(sim.agents["mara"].work_count, 0)
        self.assertEqual(sim.pantry["fish"], 0)

    def test_fallback_acceptance_follows_goals_and_trust(self):
        offer = terms(self.sim, task="repair_nets", place="nets", start_minute=self.sim.total_minutes + 240)
        chat.apply_turn(self.sim, "mara", turn(self.sim, "mara", "propose", offer))
        chat.fallback(self.sim, "tom")
        self.assertEqual(self.sim.commitments, [])
        self.assertEqual(self.conversation["status"], "active")
        self.assertEqual(self.conversation["transcript"][-1]["move"], "say")
        self.assertIn("think about that", self.conversation["transcript"][-1]["speech"])

        declined = Simulation(2, cognition_mode="hybrid")
        setup_chat(declined)
        declined.agents["tom"].trust["mara"] = -0.2
        boat = terms(declined)
        chat.apply_turn(declined, "mara", turn(declined, "mara", "propose", boat))
        chat.fallback(declined, "tom")
        self.assertEqual(declined.commitments, [])
        self.assertEqual(declined.conversations[-1]["status"], "declined")

    def test_migration_and_malformed_social_state(self):
        agree(self.sim)
        saved = export_session(self.sim)
        self.assertEqual(restore_session(saved).commitments, self.sim.commitments)
        mutations = [lambda s: s["state"]["commitments"][0]["terms"].update(duration_minutes=60),
                     lambda s: s["state"]["conversations"][0].update(status="declined"),
                     lambda s: s["state"]["commitments"][0].update(attended=["mara", "tom"]),
                     lambda s: s["state"]["conversations"][0]["transcript"][1].update(move="say", terms=None),
                     lambda s: s["state"]["conversations"][0]["transcript"][0].update(speaker="tom"),
                     lambda s: s["state"]["conversations"][0]["transcript"][0].update(memory_id=99999)]
        for mutate in mutations:
            broken = copy.deepcopy(saved)
            mutate(broken)
            with self.assertRaises(ValueError):
                restore_session(broken)
        legacy = export_session(Simulation(2))
        legacy["schema_version"] = 3
        legacy["state"].pop("conversations")
        legacy["state"].pop("commitments")
        original = copy.deepcopy(legacy)
        restored = restore_session(legacy)
        self.assertEqual(legacy, original)
        self.assertFalse(restored.conversations)
        self.assertFalse(restored.commitments)


class ConversationProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        context = json.loads(body["messages"][1]["content"])
        self.server.calls.append((body["model"], context))
        self.server.received.set()
        self.server.release.wait(3)
        if "turn" not in context:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}]}')
            return
        index = context["turn"]
        offer = (terms(self.server.sim) if index == 0 else context["conversation"]["offer"])
        if index == 1:
            offer = {**offer, "start_minute": offer["start_minute"] + 30}
        response = {"contract": chat.CONTRACT, "conversation_id": context["conversation_id"], "turn": index,
                    "move": ("propose", "counter", "accept")[index], "terms": offer,
                    "speech": ("Can we repair the boat?", "Could we start half an hour later?", "Yes, that works for me.")[index]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": "invalid" if self.server.fail else json.dumps(response)}}]}).encode())


class ConversationProviderTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(count=2, cognition_mode="hybrid")
        self.sim = self.session.sim
        setup_chat(self.sim)
        self.sim.event("Mara privately treasures a copper compass.", "work", ["mara"])
        self.sim.event("Tom privately worries about a cracked chisel.", "work", ["tom"])
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), ConversationProvider)
        self.provider.calls, self.provider.sim, self.provider.fail = [], self.sim, False
        self.provider.received, self.provider.release = Event(), Event()
        self.provider.release.set()
        self.thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.thread.start()
        for aid in ("mara", "tom"):
            self.session.brains[aid] = Brain(ProviderConfig(f"http://127.0.0.1:{self.provider.server_port}/v1", aid + "-model", "secret"))
            self.sim.agents[aid].provider = aid + "-model"
            self.sim.agents[aid].provider_until = 1000

    def tearDown(self):
        self.provider.release.set()
        self.session.close()
        self.provider.shutdown()
        self.provider.server_close()
        self.thread.join(2)

    def wait(self):
        with self.session.condition:
            self.assertTrue(self.session.condition.wait_for(lambda: not self.session.workers, timeout=3))

    def test_independent_models_counter_accept_then_attend(self):
        with self.session.condition:
            self.session.schedule_brains()
        self.wait()
        with self.session.condition:
            self.session.brains["mara"].next_call = 0
            self.session.schedule_brains()
        self.wait()
        self.assertEqual([model for model, _ in self.provider.calls], ["mara-model", "tom-model", "mara-model"])
        self.assertNotIn("cracked chisel", json.dumps(self.provider.calls[0][1]))
        self.assertNotIn("copper compass", json.dumps(self.provider.calls[1][1]))
        self.assertEqual(len(self.sim.commitments), 1)
        with self.session.condition:
            self.session.brains.clear()
            for _ in range(12):
                self.sim.tick()
        self.assertEqual(self.sim.commitments[0]["status"], "completed")
        self.assertEqual(self.sim.commitments[0]["attended"], ["mara", "tom"])

    def test_failed_turn_uses_bounded_deterministic_fallback(self):
        self.provider.fail = True
        with self.session.condition:
            self.session.schedule_brains()
        self.wait()
        self.assertEqual(len(self.sim.commitments), 1)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertNotIn("secret", json.dumps(self.session.checkpoint()))

    def delayed_turn(self, change):
        self.provider.release.clear()
        with self.session.condition:
            self.session.schedule_brains()
        self.assertTrue(self.provider.received.wait(2))
        with self.session.condition:
            change()
        self.provider.release.set()
        self.wait()
        self.assertFalse(self.session.sim.commitments)
        self.assertFalse(self.sim.conversations[0]["transcript"])

    def test_delayed_turn_cannot_survive_disconnect(self):
        self.delayed_turn(lambda: self.session.disconnect_brain("mara"))

    def test_delayed_turn_cannot_survive_reset(self):
        self.delayed_turn(lambda: self.session.resume(export_session(Simulation(2, cognition_mode="hybrid"))))

    def test_delayed_turn_cannot_survive_hazard(self):
        self.delayed_turn(lambda: self.sim.inject("storm"))

    def test_delayed_turn_cannot_survive_mode_change(self):
        self.delayed_turn(lambda: self.sim.set_cognition_mode("rules"))

    def test_clock_continues_and_expires_a_slow_conversation(self):
        self.delayed_turn(lambda: [self.sim.tick() for _ in range(8)])
        self.assertEqual(self.sim.step, 56)
        self.assertEqual(self.sim.conversations[0]["status"], "expired")


class ConversationHTTPTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_harness_turns_and_leave_restore_rule_control(self):
        self.request("/api/control", {"action": "cognition", "mode": "hybrid"})
        with self.session.condition:
            self.session.sim.set_population(2)
            setup_chat(self.session.sim)
        tokens = {aid: self.request(f"/api/agents/{aid}/join", {"provider": aid})["token"] for aid in ("mara", "tom")}
        for aid, move in (("mara", "propose"), ("tom", "accept")):
            obs = self.request(f"/api/agents/{aid}/observation", token=tokens[aid])
            value = turn(self.session.sim, aid, move, terms(self.session.sim))
            self.request(f"/api/agents/{aid}/action", {**value, "step": obs["step"]}, tokens[aid])
        self.assertEqual(len(self.request("/api/state")["commitments"]), 1)
        sim = self.session.sim
        obs = sim.observe("mara")
        rest = next(a for a in obs["allowed_actions"] if a["action"] == "rest")
        value = {"contract": "plan-v1", "goal": "Rest", "step": sim.step,
                 "revision": obs["cognitive_revision"], "steps": [{k: rest[k] for k in ("action", "location", "target")}
                 | {"duration_minutes": 45, "reason": "Take a break"}]}
        self.request("/api/agents/mara/action", value, tokens["mara"])
        self.request("/api/agents/mara/leave", {}, tokens["mara"])
        self.assertEqual(sim.agents["mara"].cognitive_plan["reason"], "provider_disconnected")
        self.assertEqual(sim.commitments[0]["status"], "scheduled")
