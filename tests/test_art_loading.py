"""Slow or interrupted art requests must not silently leave the old renderer."""

from pathlib import Path
import shutil
import subprocess
import unittest


ART = Path(__file__).resolve().parents[1] / "village_sim/static/world-view.js"

HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const files = Array.from({length:20}, (_, i) => `tile-${i}.png`);
const manifest = {tiles:Object.fromEntries(files.map((f,i)=>[i,f])),
  buildings:[],resources:[],scenery:[],characters:{}};
const elements = new Map();
for (const id of ['art-status','art-message','art-retry','scene']) {
  elements.set(id, {hidden:true,textContent:'',listeners:{},classList:{add(){}},
    addEventListener(event, fn) { this.listeners[event]=fn; }});
}
const attempts = new Map();
let failImage = () => false, failManifest = false, hangImage = false;
let active = 0, peak = 0, timeoutCount = 0;
const context = vm.createContext({
  window:{},console:{warn(){}},Map,Set,AbortController,
  matchMedia:()=>({matches:false}),requestAnimationFrame(){},
  setTimeout(fn, ms) {
    return setTimeout(()=>{if(ms === 12000) timeoutCount++; fn();}, ms === 12000 ? 100 : 0);
  }, clearTimeout,
  performance:{now:()=>0},CustomEvent:class {},
  document:{hidden:false,getElementById:id=>elements.get(id),dispatchEvent(){}},
  fetch:async()=>({ok:!failManifest,status:503,json:async()=>manifest}),
  Image:class {
    set src(url) {
      if (!url) return;
      const file = url.split('/').pop().split('?')[0];
      const count = (attempts.get(file)||0)+1;
      attempts.set(file,count);
      active++; peak=Math.max(peak,active);
      if (hangImage) return;
      setImmediate(()=>{
        active--;
        if (failImage(file,count)) this.onerror?.(); else this.onload?.();
      });
    }
  },
});
const waitFor = async predicate => {
  for (let i=0;i<1000;i++) {
    if(predicate()) return;
    await new Promise(resolve=>setTimeout(resolve,2));
  }
  throw new Error('Art loader did not settle');
};
const start = () => {
  vm.runInContext(source, context);
  // Rendering itself is covered by browser QA; these checks exercise real
  // network scheduling, failure handling, UI status and retry behaviour.
  vm.runInContext('bakeTerrain = () => ({}); drawWorld = () => {};', context);
  return context.window.VillageView;
};
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional for viewer checks")
class ArtLoadingTests(unittest.TestCase):
    def run_js(self, script):
        run = subprocess.run(
            ["node", "-e", HARNESS + "\n(async()=>{\n" + script
             + "\n})().catch(e=>{console.error(e);process.exitCode=1;});", str(ART)],
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_transient_failure_recovers_with_bounded_requests(self):
        self.run_js(r"""
failImage = (file,count) => file === files[0] && count === 1;
const view = start();
await waitFor(()=>!view.loading);
assert.equal(view.ready,true);
assert.equal(view.images.size,files.length);
assert.equal(attempts.get(files[0]),2);
assert.ok(peak<=6, `Too many simultaneous requests: ${peak}`);
assert.equal(elements.get('art-status').hidden,true);
""")

    def test_persistent_failure_is_visible_and_manual_retry_reuses_images(self):
        self.run_js(r"""
failImage = file => file === files[0];
const view = start();
await waitFor(()=>!view.loading);
assert.equal(view.ready,false);
assert.equal(attempts.get(files[0]),3);
assert.equal(elements.get('art-status').hidden,false);
assert.equal(elements.get('art-retry').hidden,false);
assert.match(elements.get('art-message').textContent,/backup view/);
const loaded = new Set(view.images.keys());
const before = new Map(attempts);
failImage = () => false;
await elements.get('art-retry').listeners.click();
assert.equal(view.ready,true);
assert.equal(view.images.size,files.length);
assert.equal(attempts.get(files[0]),4);
for (const file of loaded) assert.equal(attempts.get(file),before.get(file));
assert.equal(elements.get('art-status').hidden,true);
""")

    def test_manifest_failure_can_be_retried_without_reloading_the_page(self):
        self.run_js(r"""
failManifest = true;
const view = start();
await waitFor(()=>!view.loading);
assert.equal(view.ready,false);
assert.equal(elements.get('art-retry').hidden,false);
failManifest = false;
await elements.get('art-retry').listeners.click();
assert.equal(view.ready,true);
""")

    def test_pirate_raid_sails_in_and_strikes_before_it_leaves(self):
        self.run_js(r"""
const view = start();
await waitFor(() => !view.loading);
const beats = view.raidBeats;
const at = (name, extra = 40) => {
  let t = 0;
  for (const [n, d] of beats) {
    if (n === name) return t + extra;
    t += d;
  }
  return t + extra;
};
const crew = [
  {id:"lin", x:24, y:5, collapsedUntil:10},
  {id:"tom", x:16, y:6, collapsedUntil:10},
  {id:"mara", x:9, y:10, collapsedUntil:1},
];
view.notePirate(0, true, crew);
view.manifest = {characters:{
  lin:{actions:{idle:{s:"stand.png"}, collapsed:{s:"down.png"}}},
  tom:{actions:{idle:{s:"tom-stand.png"}, collapsed:{s:"tom-down.png"}}},
}};
const early = view.raidFrame(at("approach", 80));
assert.equal(early.phase, "approach");
assert.ok(early.boat.x > early.landing.x);
assert.equal(early.down.join(","), "");
assert.equal(early.targets.map((t) => t.id).join(","), "lin,tom");
assert.equal(view.raidPose("lin", "s", early), "stand.png");
const inland = view.raidFrame(at("inland"));
assert.equal(inland.phase, "inland");
assert.ok(inland.crew[0].x < early.landing.x);
const strike = view.raidFrame(at("strike"));
assert.equal(strike.phase, "strike");
assert.equal(strike.down.join(","), "lin,tom");
const lin = {x:(24 + 0.5) * 32, y:(5 + 0.5) * 32};
assert.ok(Math.hypot(strike.crew[0].x - lin.x, strike.crew[0].y - lin.y) < 8);
assert.equal(view.raidPose("lin", "s", strike), "down.png");
view.notePirate(at("strike"), false, []);
assert.equal(view.raidFrame(at("strike")).phase, "strike");
const shown = beats.reduce((sum, beat) => sum + beat[1], 0);
view.notePirate(shown, false, []);
assert.equal(view.raidFrame(shown).phase, "return");
const goneAt = shown + view.raidLeave.run + view.raidLeave.sail + 30;
assert.equal(view.raidFrame(goneAt), null);
view.notePirate(goneAt + 10, true, crew);
const again = view.raidFrame(goneAt + 10);
assert.equal(again.phase, "approach");
assert.ok(again.boat.x > again.landing.x);
""")

    def test_stalled_image_times_out_and_exposes_retry(self):
        self.run_js(r"""
files.splice(1);
manifest.tiles = {water:files[0]};
hangImage = true;
const view = start();
await waitFor(()=>!view.loading);
assert.equal(view.ready,false);
assert.equal(timeoutCount,3);
assert.equal(elements.get('art-retry').hidden,false);
""")
