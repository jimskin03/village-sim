import copy
import unittest

from village_sim.simulation import Simulation


class LedgerTests(unittest.TestCase):
    def working_village(self):
        sim = Simulation()
        sim.step = 48  # Day two at 06:00.
        sim.set_population(3)
        for agent in sim.agents.values():
            place = {'fisher': 'jetty', 'gardener': 'garden', 'boatwright': 'shore'}[agent.role]
            agent.x, agent.y = sim.world.point(place)
        return sim

    def test_work_outputs_are_spent_once_at_supper(self):
        sim = self.working_village()
        mara, lin = sim.agents['mara'], sim.agents['lin']
        for _ in range(4):
            sim._work(mara, 'jetty', 'silver_fish')
        for _ in range(2):
            sim._work(lin, 'garden', 'mint_bundle')
        sim.step = 95
        sim.tick()
        self.assertEqual((sim.pantry['fish'], sim.pantry['herbs']), (1, 1))
        self.assertEqual(sim.suppers[-1]['missing'], {'fish': 0, 'herbs': 0})
        sim.tick()
        self.assertEqual((sim.pantry['fish'], sim.pantry['herbs']), (1, 1))
        self.assertEqual(len(sim.suppers), 1)

    def test_empty_supper_remembers_contributors_and_changes_tomorrow(self):
        sim = Simulation()
        sim.tick()
        self.assertIn('Mara', sim.suppers[-1]['empty_handed'])
        self.assertTrue(any('came home empty-handed' in m['text'] for m in sim.agents['tom'].memory))
        for _ in range(23):
            sim.tick()
        work = sim.agents['mara'].goals[0]
        self.assertEqual(work['place'], 'shore')
        self.assertIn('Yesterday', work['title'])
        self.assertIn('jetty', work['title'])
        self.assertEqual(len(sim.reflections), 1)
        self.assertEqual(sim.reflections[0]['text'].count('.'), 2)
        self.assertEqual(sim.reflections[0]['reflected_day'], 1)

    def test_repeated_work_is_retained_alongside_its_daily_summary(self):
        sim = self.working_village()
        mara = sim.agents['mara']
        for _ in range(3):
            sim._work(mara, 'jetty', 'silver_fish')
        self.assertEqual(sim.pantry['fish'], 3)
        catches = [m for m in mara.memory if m['text'] == 'Mara caught a silver fish.']
        self.assertEqual(len(catches), 3)
        self.assertEqual(len({memory['id'] for memory in catches}), 3)
        sim.step = 119
        sim.tick()
        self.assertTrue(all(memory in mara.memory for memory in catches))
        self.assertTrue(any('3 silver fish' in m['text'] for m in mara.memory if m['kind'] == 'work_summary'))
        summary = next(m for m in mara.memory if m['kind'] == 'work_summary')
        self.assertEqual(summary['type'], 'semantic')
        self.assertEqual(summary['source_ids'], [memory['id'] for memory in catches])

    def test_storm_damage_survives_clear_weather_and_work_repairs_it(self):
        sim = self.working_village()
        sim.inject('storm')
        self.assertEqual(sim.pantry['net_condition'], 'torn')
        sim.tick()
        self.assertTrue(all(a.destination == a.home for a in sim.agents.values()))
        sim.inject('clear')
        self.assertEqual(sim.observe('mara')['suggested_action']['place'], 'nets')
        sim._work(sim.agents['tom'], 'nets', 'mended_mesh')
        self.assertEqual(sim.pantry['net_condition'], 'sound')
        self.assertEqual(sim.observe('mara')['suggested_action']['place'], 'jetty')

    def test_festival_spends_stock_once_and_occupies_guest_cottage(self):
        sim = Simulation()
        sim.pantry.update(fish=5, herbs=3)
        sim.inject('festival')
        self.assertEqual((sim.pantry['fish'], sim.pantry['herbs']), (3, 2))
        self.assertEqual(sim.visitor['place'], 'guest_home')
        self.assertIn('festival', sim.agents['mara'].relationships['tom'])
        before = dict(sim.pantry)
        sim.inject('festival')
        self.assertEqual(sim.pantry, before)
        sim.inject('clear')
        self.assertIsNone(sim.visitor)

    def test_model_slots_cannot_invent_work_or_impossible_results(self):
        sim = self.working_village()
        with self.assertRaises(ValueError):
            sim.submit('mara', {'step': sim.step, 'place': 'garden', 'activity': 'flying'})
        with self.assertRaises(ValueError):
            sim.submit('mara', {'step': sim.step, 'result': 'mint_bundle'})
        sim.submit('mara', {'step': sim.step, 'result': 'empty_net', 'mood': 'worried', 'thought': 'The crate is still empty.'})
        sim.tick()
        self.assertEqual(sim.pantry['fish'], 0)
        self.assertEqual(sim.agents['mara'].result, 'empty_net')
        sim.inject('storm')
        with self.assertRaises(ValueError):
            sim.submit('mara', {'step': sim.step, 'result': 'silver_fish'})

    def test_speech_requires_current_role_hour_and_recent_fact(self):
        sim = Simulation()
        self.assertEqual(sim.observe('mara')['speech_options'], [])
        sim.tick()
        lines = sim.observe('mara')['speech_options']
        self.assertIn('evening', lines[0])
        self.assertIn('nets', lines[0])
        self.assertIn('short', lines[0])
        with self.assertRaises(ValueError):
            sim.submit('lin', {'step': sim.step, 'speech': lines[0]})
        sim.step = 24
        with self.assertRaises(ValueError):
            sim.submit('mara', {'step': sim.step, 'speech': lines[0]})

    def test_daytime_chat_still_records_a_promise_when_recent_memory_is_full(self):
        sim = Simulation(count=2)
        sim.step = 48  # 06:00, before supper, so the chat records a promise.
        mara, tom = sim.agents['mara'], sim.agents['tom']
        fact = {
            'id': 1, 'step': 1, 'day': 1, 'time': '18:15', 'kind': 'supper',
            'text': 'Supper on day 1 used 0 fish and 0 herb bundles.',
            'agents': ['mara', 'tom'],
        }
        for agent in (mara, tom):
            agent.x, agent.y = sim.world.point('fire')
            agent.last_chat = -8
            agent.memory = [fact] + [
                {
                    'id': 2, 'step': 2, 'day': 2, 'time': '06:00', 'kind': 'conversation',
                    'text': f'{agent.name} recalled note {i}.', 'agents': [agent.id],
                }
                for i in range(7)
            ]
        sim._converse()
        self.assertIn('promised Tom', mara.relationships['tom'])
        self.assertEqual(tom.relationships['mara'], mara.relationships['tom'])

    def test_full_village_runs_several_days_without_a_conversation_crash(self):
        sim = Simulation(8, seed=3)
        for _ in range(96 * 3):
            sim.tick()
        self.assertGreaterEqual(sim.day, 4)
        self.assertTrue(any(agent.relationships for agent in sim.agents.values()))
        self.assertTrue(all(
            isinstance(fact, str) and fact
            for agent in sim.agents.values()
            for fact in agent.relationships.values()
        ))
        self.assertGreaterEqual(len(sim.reflections), 2)

    def test_hearth_and_snapshot_follow_world_clock(self):
        sim = Simulation()
        before = copy.deepcopy(sim.snapshot())
        sim.step = 47
        sim.tick()
        self.assertFalse(sim.pantry['hearth_lit'])
        sim.step = 95
        sim.tick()
        self.assertTrue(sim.pantry['hearth_lit'])
        self.assertEqual(before['suppers'], [])
        self.assertEqual(before['pantry']['fish'], 0)


if __name__ == '__main__':
    unittest.main()
