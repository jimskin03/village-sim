"""Buildless viewer regression checks using Node's standard-library VM."""

from pathlib import Path
import shutil
import subprocess
import unittest


APP = Path(__file__).resolve().parents[1] / "village_sim" / "static" / "app.js"


@unittest.skipUnless(shutil.which("node"), "Node is optional for viewer checks")
class ViewerReviewTests(unittest.TestCase):
    def run_js(self, script):
        setup = """
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const vm = require('node:vm');
"""
        run = subprocess.run(["node", "-e", setup + script, str(APP)],
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_old_story_text_is_normalized_without_losing_episodes(self):
        self.run_js(r"""
const context = vm.createContext({document: {querySelector() {}}});
vm.runInContext(source.slice(0, source.indexOf('const icon =')) +
  source.slice(source.indexOf('function journalEntries('), source.indexOf('function renderJournal(')) +
  ';globalThis.subject = {storyText, narrativeText, journalEntries};', context);
const {storyText, narrativeText, journalEntries} = context.subject;
const fact = 'Mara and Tom agreed to talk at minute 34185 for 30 minutes.';
const repeated = 'I will remember this: I will remember this: ' + fact;
assert.equal(narrativeText(repeated), 'Mara and Tom agreed to talk on day 24 at 17:45 for 30 minutes.');
assert.ok(storyText('<script>bad</script>').includes('&lt;script&gt;'));
assert.ok(!storyText('<script>bad</script>').includes('<script>'));
const records = [
 {id:1, day:24, kind:'promise', text:fact},
 {id:2, day:24, kind:'insight', text:repeated},
 {id:3, day:24, kind:'insight', text:repeated},
 {id:4, day:24, kind:'work', text:'Mara caught a fish.'},
 {id:5, day:24, kind:'work', text:'Mara caught a fish.'},
];
const before = JSON.stringify(records);
const visible = journalEntries(records);
assert.equal(visible.length, 3);
assert.equal(visible.filter(e => e.kind === 'work').length, 2);
assert.equal(JSON.stringify(records), before);
assert.equal(journalEntries([...records, {id:6, day:25, kind:'promise', text:fact}]).length, 4);
""")

    def test_default_fills_browser_and_overview_still_fits_the_whole_map(self):
        self.run_js(r"""
const elements = new Map();
const get = selector => {
 if (!elements.has(selector)) elements.set(selector, {
   clientWidth:1240, clientHeight:530, scrollLeft:0, scrollTop:0,
   attrs:{}, listeners:{}, classList:{toggle(){},add(){},remove(){}},
   style:{setProperty(key,value){ if(key === '--map-width') canvas.clientWidth = parseFloat(value); }},
   setAttribute(key,value){this.attrs[key]=value;},
   addEventListener(key,fn){this.listeners[key]=fn;},
 });
 return elements.get(selector);
};
const canvas = get('#scene-inner');
const context = vm.createContext({$:get, ResizeObserver: class {observe(){}}, window:{}});
vm.runInContext(source.slice(source.indexOf('const mapViewport ='), source.indexOf('$("#export").addEventListener')), context);
const viewport = get('#scene-viewport');
const fill = () => {
 assert.ok(canvas.clientWidth >= viewport.clientWidth - 1);
 assert.ok(canvas.clientWidth * 9/16 >= viewport.clientHeight - 1);
 assert.equal(get('#fill-screen').attrs['aria-pressed'], 'true');
 assert.equal(get('#overview').attrs['aria-pressed'], 'false');
 assert.equal(get('#map-scale').textContent, '100%');
};
const fit = () => {
 assert.ok(canvas.clientWidth <= viewport.clientWidth + 1);
 assert.ok(canvas.clientWidth * 9/16 <= viewport.clientHeight + 1);
 assert.equal(get('#overview').attrs['aria-pressed'], 'true');
 assert.equal(get('#zoom-out').disabled, true);
 assert.equal(get('#map-scale').textContent, 'Fit');
};
fill();
const initial = canvas.clientWidth;
get('#zoom').listeners.click();
assert.ok(canvas.clientWidth > initial);
assert.equal(get('#zoom-out').disabled, false);
get('#zoom-out').listeners.click();
fill();
get('#zoom-out').listeners.click();
fit();
get('#zoom').listeners.click(); get('#zoom').listeners.click();
get('#overview').listeners.click(); fit();
get('#fill-screen').listeners.click(); fill();
for (const [width,height] of [[1366,768],[1366,656],[1280,800],[390,844],[731,457]]) {
 viewport.clientWidth = width; viewport.clientHeight = height;
 context.resizeMap(); fill();
 get('#overview').listeners.click(); fit();
 get('#fill-screen').listeners.click(); fill();
}
""")


if __name__ == "__main__":
    unittest.main()
