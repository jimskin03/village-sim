// Optional Chromium checks against a disposable local server with visitors enabled.
// Set VILLAGE_SIM_URL and VILLAGE_SIM_OPERATOR_TOKEN. PLAYWRIGHT_MODULE may name
// an existing playwright-core module; it is a development tool, not an app dependency.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright-core");
const base = process.env.VILLAGE_SIM_URL || "http://127.0.0.1:8000";
const operator = process.env.VILLAGE_SIM_OPERATOR_TOKEN;
assert.ok(operator, "Set the disposable village's operator token");
const request = async (path, body, token) => {
  const response = await fetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Operator-Token": operator,
      ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const result = await response.json();
  assert.ok(response.ok, JSON.stringify(result));
  return result;
};
const original = await request("/api/state");
await request("/api/control", { action: "pause" });
let registration;
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ["--no-sandbox"] });
const errors = [];
const artifacts = process.env.ARTIFACT_DIR || join(tmpdir(), "village-viewer-review");
await mkdir(artifacts, { recursive: true });
const fit = page => page.evaluate(() => {
  const map = document.querySelector("#scene-inner").getBoundingClientRect();
  const view = document.querySelector("#scene-viewport").getBoundingClientRect();
  return map.left >= view.left - 1 && map.top >= view.top - 1
    && map.right <= view.right + 1 && map.bottom <= view.bottom + 1;
});
try {
  const name = `O'Bay-${Date.now()}`;
  registration = await request("/api/visitors/register", { name, look: "tide" });
  const observation = await request(registration.observation_url, undefined, registration.token);
  await request(registration.act_url, { step: observation.step, kind: "move", place: "garden" }, registration.token);
  // The narrow desktop viewport exercises the CSS space available at 175% zoom.
  for (const [width, height] of [[1280, 800], [390, 844], [731, 457]]) {
    const context = await browser.newContext({ viewport: { width, height },
      permissions: ["clipboard-read", "clipboard-write"] });
    const page = await context.newPage();
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(base);
    await page.waitForSelector(".sprite");
    await page.waitForFunction(() => document.querySelector("#playback-state").textContent === "Paused");
    assert.equal(await page.locator("#play").isDisabled(), true);
    assert.ok(await fit(page), "Overview fits before opening a drawer");
    if (width === 1280) {
      const replay = { schema_version: 1, world: await request("/api/world"), snapshots: [original] };
      await page.locator("#import").setInputFiles({ name: "viewer-check.json", mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(replay)) });
      await page.locator("#return-live").waitFor();
      assert.equal(await page.locator("#play").isDisabled(), false, "Spectators can replay a story");
      assert.equal(await page.locator("#resume-story").isDisabled(), true);
      await page.locator("#play").click();
      assert.equal(await page.locator("#playback-state").textContent(), "Replay playing");
      await page.locator("#return-live").click();
      assert.equal(await page.locator("#play").isDisabled(), true, "Live controls stay protected after replay");
      await page.waitForFunction(() => document.querySelector("#playback-state").textContent === "Paused");
    }
    await page.getByRole("button", { name: "Visitors", exact: true }).click();
    assert.equal(await page.locator(".visitor-card strong").last().textContent(), name);
    assert.ok((await page.locator("#visitor-ledger").innerText()).includes(name));
    for (const action of ["enable", "disable", "revoke_all"])
      assert.ok(await page.locator(`[data-visitor-action="${action}"]`).isDisabled());
    assert.equal(await page.locator("[data-expel]").count(), 0);
    await page.getByRole("button", { name: "Copy join prompt", exact: true }).click();
    const prompt = await page.evaluate(() => navigator.clipboard.readText());
    assert.ok(prompt.includes(new URL(base).origin));
    assert.ok(!prompt.includes(operator) && !prompt.includes(registration.token));
    await page.keyboard.press("Escape");
    await page.locator("#settings-open").click();
    page.once("dialog", dialog => dialog.accept("invalid-viewer-test-token"));
    await page.locator("#settings [data-operator-unlock]").click();
    await page.waitForFunction(() => document.querySelector("#toast").textContent.includes("operator token"));
    assert.ok(await page.locator("#play").isDisabled());
    page.once("dialog", dialog => dialog.accept(operator));
    await page.locator("#settings [data-operator-unlock]").click();
    await page.waitForFunction(() => !document.querySelector("#play").disabled);
    await page.keyboard.press("Escape");
    await page.locator("#sound").click();
    assert.ok((await page.locator("#sound").innerText()).includes("Sound on"));
    await page.locator("#sound").click();
    await page.locator("#zoom").click();
    assert.equal(await page.locator("#map-scale").textContent(), "150%");
    const beforeDrag = await page.locator("#scene-viewport").evaluate(el => ({
      left: el.scrollLeft, top: el.scrollTop, x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y,
      width: el.clientWidth,
    }));
    // Start in the empty right edge, away from clickable villager sprites.
    await page.mouse.move(beforeDrag.x + beforeDrag.width - 24, beforeDrag.y + 20);
    await page.mouse.down();
    await page.mouse.move(beforeDrag.x + beforeDrag.width - 64, beforeDrag.y + 50, { steps: 4 });
    await page.mouse.up();
    assert.ok(await page.locator("#scene-viewport").evaluate((el, before) =>
      el.scrollLeft !== before.left || el.scrollTop !== before.top, beforeDrag), "Map drag changes the view");
    await page.locator("#zoom-out").click();
    assert.ok(await fit(page), "Zoom out restores the whole village");
    await page.locator("#zoom").click();
    await page.locator("#overview").click();
    await page.locator("#labels").click();
    await page.getByRole("button", { name: "Journal", exact: true }).click();
    await page.waitForTimeout(100);
    assert.ok(await fit(page), "Overview fits beside/above an open drawer");
    const geometry = await page.evaluate(() => {
      const panel = document.querySelector("#journal-panel").getBoundingClientRect();
      const view = document.querySelector("#scene-viewport").getBoundingClientRect();
      const body = document.querySelector("#journal-panel .panel-body");
      body.scrollTop = body.scrollHeight;
      return { noOverflow: document.documentElement.scrollWidth <= innerWidth,
        panelFits: panel.top >= 0 && panel.bottom <= innerHeight,
        separate: view.right <= panel.left || view.bottom <= panel.top,
        scrolled: body.scrollTop + body.clientHeight >= body.scrollHeight - 1 };
    });
    assert.deepEqual(geometry, { noOverflow: true, panelFits: true, separate: true, scrolled: true });
    await page.screenshot({ path: join(artifacts, `journal-${width}.png`) });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Visitors", exact: true }).click();
    assert.equal(await page.locator("[data-expel]").count(), 1);
    assert.equal(await page.locator('[data-visitor-action="disable"]').isDisabled(), false);
    await page.screenshot({ path: join(artifacts, `visitors-${width}.png`) });
    await context.close();
    console.log(`Viewer passed at ${width}×${height}: fit, zoom, drag, drawers, scrolling, operator lock, sound, visitor and clipboard.`);
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
  if (registration) await request(registration.leave_url, {}, registration.token);
  if (!original.paused) await request("/api/control", { action: "play" });
}
