// Optional analytics UI regression checks against a disposable local village.
// Uses the same PLAYWRIGHT_MODULE, CHROMIUM_PATH, VILLAGE_SIM_URL and ARTIFACT_DIR
// settings as viewer_browser.mjs. Fixtures stay in the browser; no village is changed.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright-core");
const base = process.env.VILLAGE_SIM_URL || "http://127.0.0.1:8000";
const artifacts = process.env.ARTIFACT_DIR || join(tmpdir(), "village-analytics-review");
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const ready = () => page.waitForFunction(() => document.querySelector("#analytics-timeline").getAttribute("aria-busy") === "false"
  && !document.querySelector("#analytics-refresh").disabled);
const ids = () => page.locator("[data-analytics-event]").evaluateAll(nodes => nodes.map(node => node.dataset.analyticsEvent));
try {
  await page.goto(base);
  await page.waitForSelector(".sprite");
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await ready();
  assert.equal(await page.locator(".analytics-stat").count(), 4, "Real server report loads");
  assert.equal(await page.locator("#analytics-network").isVisible(), true, "The mind map is visible as soon as Analytics opens");
  assert.equal(await page.locator("#analytics-network").evaluate(node => Boolean(node.closest("details"))), false, "The primary map is never collapsed");
  const source = await (await fetch(base + "/api/state")).json();
  const actual = await (await fetch(base + "/api/analytics")).json();
  let revision = source.revision;
  let session = source.session_id;
  let fail = false;
  let delaySearch = "";
  let history = Array.from({ length: 95 }, (_, i) => ({ id: 1000 + i, day: 2, time: "08:00", step: i,
    kind: i % 2 ? "work" : "conversation", agents: [i % 3 ? "mara" : "tom"],
    text: i === 94 ? "A long conversation. " + "The village listened and remembered. ".repeat(18) : `Moment ${i}: ${i % 2 ? "fish sorted" : "a friendly conversation"}.` })).reverse();
  await page.route("**/api/analytics?*", async route => {
    const url = new URL(route.request().url());
    const query = url.searchParams;
    const all = history.filter(event => (!query.get("agent") || event.agents.includes(query.get("agent")))
      && (!query.has("kind") || query.getAll("kind").includes(event.kind))
      && (!query.get("search") || event.text.toLowerCase().includes(query.get("search").toLowerCase())));
    const older = all.filter(event => !query.has("before") || event.id < Number(query.get("before")));
    const events = older.slice(0, 40);
    const data = { ...actual, revision, session_id: session, events, event_count: history.length,
      matching_events: all.length, next_before: older.length > 40 ? events.at(-1).id : null,
      by_kind: { work: 47, conversation: 48 },
      branches: actual.branches.map(branch => ({ ...branch, count: history.filter(event => branch.kinds.includes(event.kind)).length })),
      residents: actual.residents.map(person => ({ ...person, by_kind: { work: 20, conversation: 20 }, event_count: 40 })) };
    if (query.get("search") === delaySearch && delaySearch) await new Promise(resolve => setTimeout(resolve, 500));
    try { await route.fulfill({ status: fail ? 503 : 200, contentType: "application/json", body: JSON.stringify(data) }); }
    catch { /* Closing a page or aborting an obsolete query can dispose its route. */ }
  });
  await page.locator("#analytics-refresh").click();
  await ready();
  assert.equal((await ids()).length, 40);
  await page.locator('#analytics-network [data-analytics-agent="mara"]').hover();
  assert.ok(await page.locator('.analytics-link.is-active[data-map-agent="mara"]').count());
  assert.equal(await page.locator('.analytics-link.is-active:not([data-map-agent="mara"])').count(), 0);
  assert.ok((await page.locator("#analytics-network-note").textContent()).includes("Mara"));
  assert.equal(await page.locator('#analytics-network [data-analytics-branch="work"] .analytics-node-count').textContent(), "20 shared");
  await page.locator(".analytics-event").first().click();
  assert.equal(await page.locator(".analytics-event").first().getAttribute("aria-expanded"), "true");
  assert.ok((await page.locator(".analytics-event-text").first().textContent()).length > 220);
  assert.equal(await page.locator('#analytics-network [data-analytics-agent="mara"].is-selected').count(), 1);
  assert.equal(await page.locator('#analytics-network [data-analytics-branch="talk"].is-selected').count(), 1);
  await page.locator("#analytics-more").click();
  await ready();
  assert.equal((await ids()).length, 80);
  await page.locator("#analytics-events").evaluate(node => { node.scrollTop = 400; });
  const before = await ids();
  revision += 100;
  history.unshift({ ...history[0], id: 1900, text: "The map is live while the log stays put." });
  await page.evaluate(({ source, revision }) => window.VillageAnalytics.update({ ...source, revision }, { recording: null, connected: true }), { source, revision });
  assert.deepEqual(await ids(), before, "Live updates retain older pages");
  assert.equal(await page.locator("#analytics-events").evaluate(node => node.scrollTop), 400);
  assert.equal(await page.locator(".analytics-event.is-selected").count(), 1);
  assert.equal(await page.locator("#analytics-refresh span").textContent(), "Show latest");
  await page.waitForFunction(() => document.querySelector(".analytics-hub-count").textContent === "96 moments");
  assert.deepEqual(await ids(), before, "Live map updates never replace the event log");
  assert.equal(await page.locator("#analytics-events").evaluate(node => node.scrollTop), 400);
  assert.equal(await page.locator('.analytics-event.is-selected').count(), 1);
  fail = true;
  await page.locator("#analytics-more").click();
  await ready();
  assert.equal(await page.locator("#analytics-refresh span").textContent(), "Retry");
  assert.deepEqual(await ids(), before);
  fail = false;
  await page.locator("#analytics-refresh").click();
  await ready();
  assert.equal((await ids()).length, 95, "Retry appends the failed page without losing earlier pages");
  assert.equal(new Set(await ids()).size, 95);
  assert.equal(await page.locator("#analytics-refresh span").textContent(), "Show latest", "Older pages do not consume new updates");
  history.unshift({ ...history[0], id: 2000, text: "A new moment arrived." });
  await page.locator("#analytics-refresh").click();
  await ready();
  assert.equal((await ids())[0], "2000");
  assert.equal((await ids()).length, 40);
  await page.locator('#analytics-network [data-analytics-branch="work"]').focus();
  await page.keyboard.press("Enter");
  await ready();
  assert.equal(await page.locator('#analytics-network [data-analytics-branch="work"]').getAttribute("aria-pressed"), "true", "Neurons can be selected using the keyboard");
  await page.locator("#analytics-agent").selectOption("mara");
  await ready();
  await page.locator("#analytics-kind").selectOption("work");
  await ready();
  assert.ok((await page.locator(".analytics-event-people").allTextContents()).every(text => text === "Mara"));
  assert.ok((await page.locator(".analytics-event-meta > span").allTextContents()).every(text => text === "Work"));
  await page.locator("#analytics-search").fill("no-match-exists");
  await page.waitForSelector(".analytics-empty strong");
  await ready();
  assert.equal((await ids()).length, 0);
  await page.locator("#analytics-clear").click();
  await ready();
  assert.equal((await ids()).length, 40);
  // Obsolete requests must not replace newer searches, even after delayed responses.
  delaySearch = "fish";
  await page.locator("#analytics-search").fill("fish");
  await page.waitForRequest(request => request.url().includes("search=fish"));
  await page.locator("#analytics-search").fill("conversation");
  await page.waitForFunction(() => document.querySelector("#analytics-filter-note").textContent.includes("conversation"));
  await ready();
  await page.waitForTimeout(600);
  assert.ok((await page.locator(".analytics-event-text").allTextContents()).every(text => text.toLowerCase().includes("conversation")));
  await page.locator("#analytics-clear").click();
  await ready();
  await page.locator("#analytics-zoom-in").click();
  await page.locator("#analytics-fit").click();
  assert.ok((await page.locator("#analytics-network-content").getAttribute("transform")).includes("scale(1)"));
  const graph = await page.locator("#analytics-network").boundingBox();
  const scale = await page.locator("#analytics-network").evaluate(svg => svg.getScreenCTM().a);
  await page.mouse.move(graph.x + graph.width / 2, graph.y + 20);
  await page.mouse.down();
  await page.mouse.move(graph.x + graph.width / 2 + 25, graph.y + 40);
  await page.mouse.up();
  const translation = await page.locator("#analytics-network-content").evaluate(group => group.transform.baseVal.consolidate().matrix.e);
  assert.ok(Math.abs(translation - 25 / scale) < 1, "Dragging follows the pointer at the rendered map scale");
  await page.locator("#analytics-fit").click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await page.locator(".analytics-signal.is-active").first().evaluate(path => getComputedStyle(path).animationName), "none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.locator("#analytics-overview").evaluate(node => { node.scrollTop = 0; });
  for (const [width, height] of [[1366, 900], [1280, 720], [390, 844], [320, 568], [731, 457]]) {
    await page.setViewportSize({ width, height });
    if (width <= 760) await page.locator('[data-analytics-view="activity"]').click();
    await page.locator("#analytics-overview").evaluate(node => { node.scrollTop = 0; });
    assert.equal(await page.locator("#analytics-network").isVisible(), true);
    const labelsFit = await page.locator("#analytics-network").evaluate(svg => {
      const view = svg.viewBox.baseVal;
      return [...svg.querySelectorAll(".analytics-node")].every(node => {
        const transform = node.transform.baseVal.getItem(0).matrix;
        const box = node.getBBox();
        return transform.e + box.x >= -1 && transform.e + box.x + box.width <= view.width + 1
          && transform.f + box.y >= -1 && transform.f + box.y + box.height <= view.height + 1;
      });
    });
    assert.ok(labelsFit, `Mind map labels fit ${width} × ${height}`);
    await page.screenshot({ path: join(artifacts, `mindmap-${width}-${height}.png`) });
    if (width <= 760) await page.locator('[data-analytics-view="events"]').click();
    await page.locator(".analytics-timeline-footer").scrollIntoViewIfNeeded();
    const fits = await page.locator("#analytics-panel").evaluate(panel => {
      const rect = panel.getBoundingClientRect();
      const events = document.querySelector("#analytics-events").getBoundingClientRect();
      const footer = document.querySelector(".analytics-timeline-footer").getBoundingClientRect();
      const close = panel.querySelector("[data-panel-close]");
      const closeRect = close.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight
        && panel.scrollWidth <= panel.clientWidth && footer.bottom <= rect.bottom && events.height >= 60
        && close.contains(document.elementFromPoint(closeRect.x + closeRect.width / 2, closeRect.y + closeRect.height / 2));
    });
    assert.ok(fits, `Analytics controls and event log fit ${width} × ${height}`);
    await page.screenshot({ path: join(artifacts, `analytics-${width}-${height}.png`) });
    if (width === 390) {
      await page.locator('[data-analytics-view="activity"]').click();
      await page.locator('#analytics-network [data-analytics-branch="work"]').click();
      await ready();
      assert.equal(await page.locator("#analytics-panel").getAttribute("data-view"), "activity", "Selecting a neuron keeps the mind map visible");
      await page.locator('[data-analytics-view="events"]').click();
      await page.locator("#analytics-clear").click();
      await ready();
    }
  }
  await page.getByRole("button", { name: "Close analytics" }).focus();
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#analytics-panel").isVisible(), false);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.panel), "analytics-panel");
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await ready();
  assert.equal(await page.locator("#analytics-panel").getAttribute("data-view"), "activity");
  await page.locator('[data-analytics-view="events"]').click();
  await page.locator("#analytics-search").fill("fish");
  await page.waitForRequest(request => request.url().includes("search=fish"));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);
  assert.equal(await page.locator("#analytics-timeline").getAttribute("aria-busy"), "false");
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await ready();
  assert.equal(await page.locator("#analytics-search").inputValue(), "fish");
  // Story switches discard filters and old events, including while the menu is closed.
  await page.keyboard.press("Escape");
  session = "analytics-test-new-session";
  await page.evaluate(({ source, session, revision }) => window.VillageAnalytics.update({ ...source, session_id: session, revision }, { recording: null, connected: true }), { source, session, revision });
  fail = true;
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await ready();
  assert.equal(await page.locator("#analytics-search").inputValue(), "");
  assert.equal(await page.locator(".analytics-stat").count(), 0, "A new story cannot display stale totals on error");
  assert.equal(await page.locator("#analytics-refresh span").textContent(), "Retry");
  await page.evaluate(({ source, session, revision }) => window.VillageAnalytics.update({ ...source, session_id: session, revision }, { recording: null, connected: true }), { source, session, revision });
  assert.equal(await page.locator("#analytics-refresh").isEnabled(), true, "Updates do not repeatedly retry failed requests");
  fail = false;
  await page.locator("#analytics-refresh").click();
  await ready();
  assert.equal((await ids()).length, 40);
  const replay = { ...source, events: history.slice(0, 12).reverse() };
  delete replay.event_count;
  await page.evaluate(replay => window.VillageAnalytics.update(replay, { recording: {}, replayIndex: 0, connected: true }), replay);
  await ready();
  await page.locator('[data-analytics-view="events"]').click();
  await page.locator("#analytics-agent").selectOption("mara");
  await ready();
  assert.equal(await page.locator(".analytics-stat strong").first().textContent(), "12", "Replay total is independent of filters");
  await page.evaluate(replay => window.VillageAnalytics.update({ ...replay, event_count: 100 }, { recording: {}, replayIndex: 1 }), replay);
  await ready();
  assert.ok((await page.locator("#analytics-status").textContent()).includes("recent window"));
  // A small or empty village still has a readable hub and keeps new event kinds.
  await page.evaluate(source => window.VillageAnalytics.update({ ...source, agents: source.agents.slice(0, 1),
    events: [{ id: 1, kind: "future_kind", day: 1, time: "18:00", text: "A new thread.", agents: [source.agents[0].id] }], event_count: 1,
  }, { recording: {}, replayIndex: 2 }), source);
  await ready();
  assert.equal(await page.locator(".analytics-node-agent").count(), 1);
  assert.equal(await page.locator('[data-analytics-branch="other"]').first().count(), 1);
  assert.equal(await page.locator(".analytics-link").count(), 1);
  await page.evaluate(source => window.VillageAnalytics.update({ ...source, agents: [], events: [], event_count: 0 }, { recording: {}, replayIndex: 3 }), source);
  await ready();
  assert.equal(await page.locator(".analytics-node-agent").count(), 0);
  assert.equal(await page.locator(".analytics-hub-count").textContent(), "0 moments");
  assert.deepEqual(errors, []);
  console.log("Analytics browser checks passed: neuron map, live connections, filtering, paging, races, retries, replay, keyboard, panning, reduced motion and five viewport sizes.");
  console.log(`Screenshots: ${artifacts}`);
} finally {
  await browser.close();
}
