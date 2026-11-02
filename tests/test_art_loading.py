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
