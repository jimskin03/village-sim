"""Exercise the buildless visitor UI with Node's standard-library VM."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / "village_sim" / "static"


class PanelParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.panels = []
        self.button_panels = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.panels.append(attrs.get("id"))
        if attrs.get("id") == "copy-join-prompt":
            self.button_panels.append(list(self.panels))

    def handle_endtag(self, tag):
        if tag == "section":
            self.panels.pop()


class VisitorViewerTests(unittest.TestCase):
    def test_join_button_belongs_to_visitors_panel(self):
        parser = PanelParser()
        parser.feed((STATIC / "index.html").read_text())
        self.assertEqual(parser.button_panels, [["visitors-panel"]])

    @unittest.skipUnless(shutil.which("node"), "Node is optional for viewer checks")
    def test_join_copy_invites_secrecy_closed_replay_and_clipboard_fallback(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const elements = new Map();
const element = (selector) => {
  if (!elements.has(selector)) elements.set(selector, {
    hidden: true, value: '', textContent: '', innerHTML: '',
    addEventListener() {}, querySelectorAll() { return []; },
    focus() { this.focused = true; }, select() { this.selected = true; },
  });
  return elements.get(selector);
};
let copied = [];
const secrets = ['operator-test-secret', 'visitor-test-secret', 'invite-test-secret'];
const context = vm.createContext({
  location: { origin: 'https://village.example:8443' },
  liveState: {
    visitors_enabled: true, visitor_invite_required: false,
    operator_token: secrets[0], token: secrets[1], invite: secrets[2],
  },
  state: { agents: [], visiting: { visitors: [{
    id: 'visitor-1', name: "O'Bay-7", look: 'tide', color: '#4fb6d8',
    status: 'active', activity: 'arriving', intent: 'see the bay',
  }], ledger: [] } },
  recording: null, world: { places: [] },
  document: { querySelector: element, querySelectorAll() { return []; } },
  navigator: { clipboard: { async writeText(text) { copied.push(text); } } },
  sessionStorage: { getItem() { throw Error('Must not read credentials'); } },
  toast() {},
});
vm.runInContext(
  source.slice(0, source.indexOf('const icon =')) +
  source.slice(source.indexOf('function visitorJoinPrompt()'), source.indexOf('function survivalBars(')),
  context,
);
(async () => {
  context.renderVisitors();
  assert.equal(element('#copy-join-prompt').disabled, false);
  assert.ok(element('#visitor-list').innerHTML.includes('O&#39;Bay-7'));
  await context.copyVisitorJoinPrompt();
  assert.equal(copied.length, 1);
  const prompt = copied[0];
  for (const suffix of ['/api/visitors/register', '/api/visitors/<VISITOR_ID>/observation',
                        '/api/visitors/<VISITOR_ID>/act', '/api/visitors/<VISITOR_ID>/leave', '/docs']) {
    assert.ok(prompt.includes(context.location.origin + suffix), suffix);
  }
  assert.ok(prompt.includes('{"name": "<CHOOSE A NAME>", "look": "<OPTIONAL>"}'));
  assert.ok(prompt.includes('8-step lease'));
  assert.ok(prompt.includes('may help or harm'));
  assert.ok(prompt.includes('Authorization: Bearer <VISITOR_TOKEN>'));
  assert.ok(!prompt.includes('"invite"'));
  for (const secret of secrets) assert.ok(!prompt.includes(secret));

  context.liveState.visitor_invite_required = true;
  await context.copyVisitorJoinPrompt();
  assert.ok(copied[1].includes('"invite"'));
  for (const secret of secrets) assert.ok(!copied[1].includes(secret));

  context.liveState.visitors_enabled = false;
  context.renderVisitors();
  assert.equal(element('#copy-join-prompt').disabled, true);
  assert.match(element('#visitor-join-note').textContent, /Open the village/);
  await context.copyVisitorJoinPrompt();
  assert.equal(copied.length, 2);

  context.liveState.visitors_enabled = true;
  context.recording = {};
  context.renderVisitors();
  assert.equal(element('#copy-join-prompt').disabled, true);
  assert.match(element('#visitor-join-note').textContent, /live village/);
  await context.copyVisitorJoinPrompt();
  assert.equal(copied.length, 2);

  context.recording = null;
  context.navigator.clipboard.writeText = async () => { throw Error('Denied'); };
  await context.copyVisitorJoinPrompt();
  const field = element('#visitor-join-prompt');
  assert.equal(field.hidden, false);
  assert.equal(field.value, context.visitorJoinPrompt());
  assert.equal(field.focused, true);
  assert.equal(field.selected, true);
  delete context.navigator.clipboard;
  await context.copyVisitorJoinPrompt();
  assert.equal(field.value, context.visitorJoinPrompt());
  context.liveState.visitors_enabled = false;
  context.renderVisitors();
  assert.equal(field.hidden, true);
  assert.equal(field.value, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        run = subprocess.run(["node", "-e", script, str(STATIC / "app.js")],
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
