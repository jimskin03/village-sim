"""Event themes start on a live change and stay quiet when a recording is scrubbed."""

from pathlib import Path
import shutil
import subprocess
import unittest


SOUND = Path(__file__).resolve().parents[1] / "village_sim/static/sound.js"

HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({
  window: {},
  document: {
    readyState: 'complete',
    hidden: false,
    querySelector() { return null; },
    addEventListener() {},
    removeEventListener() {},
  },
});
vm.runInContext(source, context);
const cues = context.window.villageSound.cues;
const base = { step: 4, minute: 900, storm: false, festival: false, pirate: false, calm: false };
const at = (patch) => ({ ...base, ...patch });
const heard = (before, after) => cues(before, after).join(',');
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional for viewer checks")
class EventThemeTests(unittest.TestCase):
    def run_js(self, script):
        run = subprocess.run(
            ["node", "-e", HARNESS + script, str(SOUND)],
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_each_event_has_a_theme_when_it_begins(self):
        self.run_js(r"""
assert.equal(heard(at(), at({ pirate: true })), 'pirate');
assert.equal(heard(at(), at({ step: 5, storm: true })), 'storm');
assert.equal(heard(at(), at({ festival: true })), 'festival');
assert.equal(heard(at({ storm: true, festival: true }), at({ calm: true })), 'calm');
assert.equal(heard(at({ minute: 1070 }), at({ step: 5, minute: 1080 })), 'supper');
""")

    def test_scrubbing_and_a_second_click_do_not_replay_the_theme(self):
        self.run_js(r"""
assert.equal(heard(at(), at({ step: 9, pirate: true })), '');
assert.equal(heard(at({ pirate: true }), at({ pirate: true })), '');
assert.equal(heard(at({ minute: 1070 }), at({ minute: 1080 })), '');
assert.equal(heard(null, at({ storm: true })), '');
""")
