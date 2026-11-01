"use strict";
const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
// Visitor words arrive wrapped in fixed delimiters; show them as quotations.
// The text is escaped first, and visitors cannot type < or >, so only the
// server's own delimiters become markup.
function narrativeText(value) {
  return String(value ?? "")
    .replace(/^(?:I will remember this:\s*)+/, "")
    .replace(/\bat minute (\d+)\b/g, (_, total) => {
      const minute = Number(total) % 1440;
      return `on day ${Math.floor(Number(total) / 1440) + 1} at ${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
    });
}
const storyText = (value) =>
  esc(narrativeText(value))
    .replaceAll("&lt;&lt;visitor-speech&gt;&gt;", '<q class="visitor-quote">')
    .replaceAll("&lt;&lt;/visitor-speech&gt;&gt;", "</q>");
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
let state = null,
  liveState = null,
  world = null,
  selected = "mara",
  tab = "story";
let recording = null,
  replayIndex = 0,
  replayPlaying = false,
  replayTimer = null;
let connected = false,
  hasConnected = false,
  toastTimer,
  showLabels = false;
let brainAgent = null;
let operatorUnlocked = false,
  operatorRequirement = null;
let activePanel = null,
  panelTrigger = null;
const color = (value) => (/^#[0-9a-f]{6}$/i.test(value) ? value : "#b6c6a2");
const portrait = (a) =>
  `<span class="portrait" style="--agent-color:${color(a.color)}" aria-hidden="true"></span>`;

function openPanel(id, trigger = document.activeElement) {
  const panel = document.getElementById(id);
  if (!panel || activePanel === id) return;
  document.querySelectorAll(".map-panel").forEach((other) => {
    other.hidden = other !== panel;
  });
  document.querySelectorAll("[data-panel]").forEach((button) => {
    button.setAttribute("aria-expanded", String(button.dataset.panel === id));
  });
  activePanel = id;
  document.body.classList.add("panel-open");
  panelTrigger = trigger;
  panel.querySelector("[data-panel-close]").focus({ preventScroll: true });
}
function closePanel() {
  if (!activePanel) return;
  const panel = document.getElementById(activePanel);
  const focusInPanel = panel.contains(document.activeElement);
  panel.hidden = true;
  document.querySelectorAll("[data-panel]").forEach((button) => {
    button.setAttribute("aria-expanded", "false");
  });
  if (focusInPanel) {
    const trigger = panelTrigger?.isConnected && !panelTrigger.closest("[hidden]")
      ? panelTrigger
      : document.querySelector(`[data-panel="${activePanel}"]`);
    trigger?.focus({ preventScroll: true });
  }
  activePanel = null;
  panelTrigger = null;
  document.body.classList.remove("panel-open");
}
document.querySelectorAll("[data-panel]").forEach((button) => {
  button.addEventListener("click", () => {
    if (activePanel === button.dataset.panel) closePanel();
    else openPanel(button.dataset.panel, button);
  });
});
document.querySelectorAll("[data-panel-close]").forEach((button) => {
  button.addEventListener("click", closePanel);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && activePanel && !$("dialog[open]")) {
    event.preventDefault();
    closePanel();
  }
});

function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}
async function post(path, data, retried = false) {
  const headers = { "Content-Type": "application/json" };
  const operator = sessionStorage.getItem("village-operator-token");
  if (operator) headers["X-Operator-Token"] = operator;
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (response.status === 401 && result.operator_token_required && !retried) {
    sessionStorage.removeItem("village-operator-token");
    operatorUnlocked = false;
    renderOperatorControls();
    const entered = window.prompt("This village is protected. Enter the operator token printed in the server console:");
    if (entered && entered.trim()) {
      sessionStorage.setItem("village-operator-token", entered.trim());
      await checkOperator();
      return post(path, data, true);
    }
  }
  if (!response.ok)
    throw new Error(
      result.error || "The village could not complete that request.",
    );
  return result;
}
async function control(action, extra = {}) {
  try {
    await post("/api/control", { action, ...extra });
  } catch (error) {
    toast(error.message);
  }
}
async function checkOperator() {
  const token = sessionStorage.getItem("village-operator-token");
  try {
    const response = await fetch("/api/operator", {
      headers: token ? { "X-Operator-Token": token } : {},
    });
    const result = await response.json();
    operatorUnlocked = response.ok && result.operator === true;
    if (!operatorUnlocked && token) sessionStorage.removeItem("village-operator-token");
    if (state && !recording) render(liveState);
    else renderOperatorControls();
    return result;
  } catch {
    operatorUnlocked = false;
    renderOperatorControls();
    return { error: "Could not check operator access. Try again when connected." };
  }
}
function renderOperatorControls() {
  document.querySelectorAll("[data-operator-control]").forEach((element) => {
    element.disabled = recording ? !element.hasAttribute("data-replay-control") : !operatorUnlocked;
    element.title = !recording && !operatorUnlocked
      ? "Unlock operator controls with the token from the server console"
      : element.getAttribute("aria-label") || "Operator control";
  });
  $("#resume-story").disabled = !operatorUnlocked || !recording?.session;
  document.querySelectorAll(".operator-status").forEach((element) => {
    element.textContent = operatorUnlocked
      ? "Operator controls unlocked for this tab."
      : "Viewing only. Village controls are locked; unlock with the operator token from the server console.";
  });
  document.querySelectorAll("[data-operator-unlock]").forEach((button) => {
    button.hidden = operatorUnlocked;
  });
}
document.querySelectorAll("[data-operator-unlock]").forEach((button) => {
  button.addEventListener("click", async () => {
    const token = window.prompt("Enter the operator token printed in the server console:");
    if (!token?.trim()) return;
    sessionStorage.setItem("village-operator-token", token.trim());
    const result = await checkOperator();
    if (!operatorUnlocked) toast(result.error || "The operator token was not accepted.");
    else toast("Operator controls unlocked.");
  });
});
function connectionLabel() {
  $(".connection").classList.toggle("offline", !connected);
  $("#connection-label").textContent = connected
    ? "World is running"
    : hasConnected ? "Reconnecting…" : "Connecting…";
  if (connected && liveState?.paused)
    $("#connection-label").textContent = "World is paused";
  $("#playback-state").textContent = recording
    ? replayPlaying ? "Replay playing" : "Replay paused"
    : !connected ? hasConnected ? "Reconnecting…" : "Connecting…"
    : liveState?.paused ? "Paused" : "Running";
}
function acceptLive(next) {
  if (liveState && next.revision < liveState.revision) return;
  if (next.persistence_error && next.persistence_error !== liveState?.persistence_error)
    toast(next.persistence_error);
  liveState = next;
  if (operatorRequirement !== next.operator_required) {
    operatorRequirement = next.operator_required;
    operatorUnlocked = false;
    checkOperator();
  }
  $("#persistence-status").textContent = next.persistence_error
    || "The village is saved after every step. Automatic replay after a restart also needs the recording archive beside the checkpoint.";
  $("#persistence-status").classList.toggle("save-error", Boolean(next.persistence_error));
  if (!recording) render(next);
}
function render(next) {
  state = next;
  if (!state.agents.some((a) => a.id === selected))
    selected = state.agents[0]?.id;
  const hour = Math.floor(state.minute / 60);
  const period =
    hour < 6
      ? "NIGHT"
      : hour < 12
        ? "MORNING"
        : hour < 18
          ? "AFTERNOON"
          : "EVENING";
  const light = hour < 6 || hour >= 20 ? "night"
    : hour < 11 ? "morning" : hour < 17 ? "noon" : "dusk";
  $("#scene").dataset.light = light;
  $("#hearth-light").hidden = !(state.pantry?.hearth_lit && (hour >= 18 || hour < 6));
  $(".scene-moon").textContent = light === "night" ? "☾" : light === "dusk" ? "◒" : "☀";
  $("#day").textContent = `DAY ${state.day} · ${period}`;
  $("#clock-time").textContent = state.time;
  $("#weather-text").textContent =
    state.weather === "storm"
      ? "Passing rain"
      : state.festival
        ? "Festival lanterns"
        : "Clear skies";
  $("#scene-caption").textContent =
    state.weather === "storm"
      ? "Rain on the roofs. A little time for shelter."
      : state.festival
        ? "Good company, under a hundred little lights."
        : hour < 6
          ? "A quiet village. The tide keeps its own time."
          : light === "morning"
            ? "Cool air over the bay. A new day’s work begins."
            : light === "noon"
              ? "Clear light on the water. Supper starts here."
              : light === "dusk"
                ? "Gold on the roofs. Time to count the supper crate."
                : "Lanterns on. The world carries on.";
  $("#scene-mode").textContent = recording
    ? "A RECORDED STORY"
    : state.paused
      ? "A MOMENT OF STILLNESS"
      : "THE VILLAGE, LIVE";
  $("#population-caption").textContent =
    `${state.agents.length} villager${state.agents.length === 1 ? "" : "s"}, following their own rhythm`;
  $("#step-caption").textContent = `Step ${state.step}`;
  $("#resident-count").textContent = String(state.agents.length).padStart(
    2,
    "0",
  );
  $("#population").value = state.agents.length;
  $("#cognition-mode").value = state.cognition_mode || "rules";
  $("#cognition-mode").disabled = Boolean(recording);
  if (!recording) $("#speed").value = state.interval;
  $("#play").innerHTML = icon(
    (recording ? !replayPlaying : state.paused) ? "play" : "pause",
  );
  $("#play").setAttribute(
    "aria-label",
    (recording ? !replayPlaying : state.paused)
      ? "Resume simulation"
      : "Pause simulation",
  );
  $("#play").title = $("#play").getAttribute("aria-label");
  $("#speed").disabled = Boolean(recording);
  $("#population").disabled = Boolean(recording);
  $("#reset").disabled = Boolean(recording);
  document.querySelectorAll("[data-event]").forEach((b) => {
    b.disabled = Boolean(recording);
  });
  renderStateMarkers();
  renderSprites();
  renderCards();
  renderDetails();
  renderJournal();
  renderVisitors();
  renderOperatorControls();
  connectionLabel();
  villageSound.follow(state);
}
function placePosition(place) {
  return `left:${((place.x + 0.5) * 32 / 1280) * 100}%;top:${((place.y + 0.5) * 32 / 720) * 100}%`;
}
function renderStateMarkers() {
  const pantry = state.pantry;
  const container = $("#state-markers");
  if (!pantry) { container.innerHTML = ""; return; }
  const need = state.agents.length;
  const markers = [
    { place: "jetty", icon: "▤", text: `Crate · ${pantry.fish} fish`, short: pantry.fish < need,
      note: state.weather === "storm" ? "Jetty closed in the storm" : `Supper needs ${need} fish` },
    { place: "garden", icon: "❧", text: `Rack · ${pantry.herbs} herbs`, short: pantry.herbs < Math.ceil(need / 3),
      note: `Supper needs ${Math.ceil(need / 3)} herb bundles` },
    { place: "shore", icon: "⚓", text: `Boats · ${pantry.boat_condition}`, short: pantry.boat_condition !== "sound",
      note: "The village boat condition" },
    { place: "nets", icon: "⌗", text: `Net · ${pantry.net_condition}`, short: pantry.net_condition !== "sound",
      note: "A damaged net needs mending" },
    { place: "fire", icon: pantry.hearth_lit ? "♨" : "○", text: pantry.hearth_lit ? "Hearth lit" : "Hearth resting",
      note: "The hearth is lit after dark" },
  ];
  if (state.collection_centers) {
    const centers = state.collection_centers;
    markers.push(
      { place: "food_center", icon: "▤", text: `Food · ${pantry.fish} fish · ${pantry.herbs} herbs`,
        short: centers.food.low, note: `${centers.food.level} food points; daily need ${centers.food.daily_need} for ${need} villagers` },
      { place: "water_center", icon: "◉", text: `Water · ${pantry.water} units`,
        short: centers.water.low, note: `Daily need ${centers.water.daily_need} water units for ${need} villagers` },
    );
    for (const [id, source] of Object.entries(state.water_sources || {})) {
      markers.push({ place: id, icon: "◉", text: `${id === "well" ? "Well" : "Rain barrel"} · ${source.supply}`,
        short: source.fouled_until > state.step,
        note: source.fouled_until > state.step ? "Temporarily fouled; fresh water returns soon" : "Fresh water replenishes every step" });
    }
  }
  if (state.visitor) markers.push({ place: "guest_home", icon: "✧",
    text: `${state.visitor.name || "Festival guest"} · visiting`, note: "A festival visitor is staying at the guest cottage" });
  container.innerHTML = markers.map((marker) => {
    const place = world.places.find((p) => p.id === marker.place);
    if (!place) return "";
    return `<span class="state-marker${marker.short ? " shortage" : ""}" style="${placePosition(place)}" tabindex="0" aria-label="${esc(marker.text)}. ${esc(marker.note)}" title="${esc(marker.text)}. ${esc(marker.note)}"><span aria-hidden="true">${marker.icon}</span> ${esc(marker.text)}${marker.short ? '<span class="sr-only"> · needs attention</span>' : ""}</span>`;
  }).join("");
  if (state.visitor) {
    const place = world.places.find((p) => p.id === "guest_home");
    if (place) container.insertAdjacentHTML("beforeend", `<span class="festival-visitor" style="${placePosition(place)};--agent-color:#dfc99b" role="img" aria-label="${esc(state.visitor.name || "A visitor")} at the guest cottage"><span class="sprite-shadow"></span><span class="sprite-legs"></span><span class="sprite-body"></span><span class="sprite-head"></span></span>`);
  }
}
function renderSprites() {
  const container = $("#villagers");
  container.querySelectorAll(".sprite:not(.visitor-sprite)").forEach((el) => {
    if (!state.agents.some((a) => a.id === el.dataset.id)) el.remove();
  });
  renderVisitorSprites(container);
  state.agents.forEach((a) => {
    let el = container.querySelector(`[data-id="${CSS.escape(a.id)}"]`);
    if (!el) {
      el = document.createElement("button");
      el.dataset.id = a.id;
      el.innerHTML =
        '<span class="sprite-shadow"></span><span class="sprite-legs"></span><span class="sprite-body"></span><span class="sprite-head"></span><span class="sprite-name"></span><span class="sprite-brain"></span><span class="sprite-message" hidden><span class="speech" hidden></span><span class="thought" hidden></span></span>';
      el.addEventListener("click", () => selectAgent(a.id));
      container.append(el);
    }
    el.className = `sprite${a.id === selected ? " selected" : ""}${a.activity.startsWith("walking") ? " walking" : ""}${a.collapsed_until ? " collapsed" : ""}`;
    el.style.left = `${(((a.x + 0.5) * 32) / 1280) * 100}%`;
    el.style.top = `${(((a.y + 0.5) * 32) / 720) * 100}%`;
    el.style.setProperty("--agent-color", color(a.color));
    el.setAttribute("aria-label", `${a.name}, ${a.activity}. Select villager.`);
    el.setAttribute("aria-pressed", String(a.id === selected));
    el.querySelector(".sprite-name").textContent = a.name;
    const brain = !recording && state.brains?.[a.id];
    const brainLabel = brain?.status === "thinking" ? "Thinking…"
      : brain?.status === "ready" ? "AI connected" : state.cognition_mode === "hybrid" ? "Planning" : "Routine";
    const badge = el.querySelector(".sprite-brain");
    badge.textContent = brainLabel;
    badge.title = brain ? "Connected AI provider" : state.cognition_mode === "hybrid"
      ? "Planning and conversations, with local replies when no AI is connected"
      : "Following a daily routine without an AI provider";
    badge.classList.toggle("thinking", brain?.status === "thinking");
    const speech = el.querySelector(".speech"), thought = el.querySelector(".thought");
    speech.hidden = !a.speech;
    speech.textContent = a.speech || "";
    thought.hidden = !a.thought;
    thought.textContent = a.thought ? `Thinking: ${a.thought}` : "";
    el.querySelector(".sprite-message").hidden = !(a.speech || a.thought);
    el.setAttribute("aria-label", `${a.name}, ${a.activity}. ${brainLabel}.${a.thought ? ` Thinking: ${a.thought}` : ""} Select villager.`);
    el.title = `${a.name} · ${a.role}\n${a.activity}${a.mood ? ` · ${a.mood}` : ""}\n${brainLabel}${a.speech ? `\n${a.speech}` : ""}`;
    el.style.zIndex = a.speech || a.thought
      ? 20
      : a.id === selected
        ? 10
        : 2 + Math.floor(a.y / 4);
  });
}
function activeVisitors() {
  return (state.visiting?.visitors || []).filter((v) => v.status === "active");
}
function renderVisitorSprites(container) {
  const present = activeVisitors();
  container.querySelectorAll(".visitor-sprite").forEach((el) => {
    if (!present.some((v) => v.id === el.dataset.visitor)) el.remove();
  });
  present.forEach((v) => {
    let el = container.querySelector(`[data-visitor="${CSS.escape(v.id)}"]`);
    if (!el) {
      el = document.createElement("span");
      el.dataset.visitor = v.id;
      el.setAttribute("role", "img");
      el.innerHTML =
        '<span class="sprite-shadow"></span><span class="sprite-legs"></span><span class="sprite-body"></span><span class="sprite-head"></span><span class="sprite-name"></span><span class="visitor-tag">visitor</span>';
      container.append(el);
    }
    el.className = `sprite visitor-sprite${v.activity.startsWith("walking") ? " walking" : ""}`;
    el.style.left = `${(((v.x + 0.5) * 32) / 1280) * 100}%`;
    el.style.top = `${(((v.y + 0.5) * 32) / 720) * 100}%`;
    el.style.setProperty("--agent-color", v.color);
    el.style.zIndex = 2 + Math.floor(v.y / 4);
    el.querySelector(".sprite-name").textContent = v.name;
    el.setAttribute("aria-label", `${v.name}, a visitor, ${v.activity}`);
    el.title = `${v.name}, a visitor\n${v.activity}`;
  });
}
function visitorJoinPrompt() {
  const origin = location.origin;
  const invite = liveState?.visitor_invite_required
    ? '\nAn invite code is required. Ask the human for it and add the field "invite" to the registration JSON. The code is not included here.'
    : "";
  return `Visit Lantern Bay, a small fishing village, using HTTP requests from your harness.
Session URL: ${origin}
You choose your own goal and may help or harm the village.

Register: POST ${origin}/api/visitors/register
Content-Type: application/json
{"name": "<CHOOSE A NAME>", "look": "<OPTIONAL>"}
Choose a unique name of 1-24 letters, digits, spaces, hyphens or apostrophes. Replace <OPTIONAL> with lantern, tide, moss, ember, dusk or pearl, or omit look. You may add intent (up to 200 characters).${invite}

Read the returned welcome summary. Keep the one-time token private and use the returned visitor_id in the endpoints below. This visitor token is your pass.
Observe: GET ${origin}/api/visitors/<VISITOR_ID>/observation
Act: POST ${origin}/api/visitors/<VISITOR_ID>/act
Leave: POST ${origin}/api/visitors/<VISITOR_ID>/leave with {}
For these three requests send Authorization: Bearer <VISITOR_TOKEN>. For POST requests also send Content-Type: application/json.
Fetch an observation first. Act with {"step": <CURRENT_STEP>, "kind": "move", "place": "garden"}, or choose another action and its fields from allowed_actions. Movement takes simulation steps; wait until here is your destination before working. You can move, talk, fish, garden, repair, give gifts, influence villagers or sabotage supplies where the world permits.
You have an 8-step lease, renewed by observations and successful actions. Poll observations before it expires, including while waiting to arrive. Limits: 2 actions per step and 6 pantry units per day, shared by work, gift, help and sabotage. Read error messages; refresh on stale actions and wait for a later step after a rate limit.
Full docs: ${origin}/docs`;
}

function renderVisitors() {
  const visiting = state.visiting || { visitors: [], ledger: [] };
  const enabled = Boolean(liveState?.visitors_enabled);
  $("#visitor-status").textContent = recording
    ? "Showing visitors from the recording."
    : enabled
      ? `Lantern Bay is welcoming visiting agents${liveState?.visitor_invite_required ? " with an invite code" : ""}. Register at POST /api/visitors/register.`
      : "Lantern Bay is closed to visitors. Residents carry on as usual.";
  const joinReason = recording
    ? "Return to the live village to copy a join prompt."
    : !enabled
      ? "Open the village to visitors to copy a join prompt."
      : "Copy this prompt into an agent harness. The agent chooses its name and goal.";
  $("#copy-join-prompt").disabled = Boolean(recording) || !enabled;
  $("#copy-join-prompt").title = joinReason;
  $("#visitor-join-note").textContent = joinReason;
  if (recording || !enabled) {
    $("#visitor-join-prompt").hidden = true;
    $("#visitor-join-prompt").value = "";
  }
  document.querySelectorAll("[data-visitor-action]").forEach((button) => {
    const action = button.dataset.visitorAction;
    button.disabled = !operatorUnlocked || Boolean(recording) || (action === "enable" ? enabled : !enabled && action === "disable");
    button.title = !operatorUnlocked ? "Unlock operator controls with the token from the server console"
      : action === "disable" ? "Closes the village and sends every visitor away"
      : action === "revoke_all" ? "Revoke every visitor pass" : "Allow outside agents to register";
  });
  const present = visiting.visitors.filter((v) => v.status === "active");
  $("#visitor-list").innerHTML = present.map((v) =>
    `<div class="visitor-card" style="--agent-color:${esc(v.color)}"><span class="visitor-swatch" aria-hidden="true"></span><span class="visitor-info"><strong>${esc(v.name)}</strong> <small>visitor · ${esc(v.look)}</small><br><span>${esc(v.activity)}</span>${v.intent ? `<br><small>Says they came to: “${esc(v.intent)}”</small>` : ""}</span>${recording || !operatorUnlocked ? "" : `<button data-expel="${esc(v.id)}" aria-label="Ask ${esc(v.name)} to leave">Expel</button>`}</div>`,
  ).join("") || '<p class="empty-note">No visitors right now.</p>';
  $("#visitor-list").querySelectorAll("[data-expel]").forEach((button) =>
    button.addEventListener("click", () => visitorControl("expel", { visitor_id: button.dataset.expel })));
  const names = Object.fromEntries(visiting.visitors.map((v) => [v.id, v.name]));
  const residentName = (id) => state.agents.find((a) => a.id === id)?.name || world.places.find((p) => p.id === id)?.name || id;
  $("#visitor-ledger").innerHTML = [...visiting.ledger].reverse().map((row) =>
    `<p class="ledger-row"><small>DAY ${esc(row.day)} · ${esc(row.time)}</small>${esc(row.name || names[row.visitor] || row.visitor)} · ${esc(String(row.kind).replaceAll("_", " "))}${row.target ? ` → ${esc(residentName(row.target))}` : ""} · <em>${esc(row.outcome)}</em></p>`,
  ).join("") || '<p class="empty-note">Nothing has happened yet.</p>';
}
async function visitorControl(action, extra = {}) {
  try {
    const next = await post("/api/visitors/control", { action, ...extra });
    acceptLive(next);
  } catch (error) {
    toast(error.message);
  }
}
document.querySelectorAll("[data-visitor-action]").forEach((button) =>
  button.addEventListener("click", () => visitorControl(button.dataset.visitorAction)));
async function copyVisitorJoinPrompt() {
  if (recording || !liveState?.visitors_enabled) return;
  const text = visitorJoinPrompt();
  try {
    await navigator.clipboard.writeText(text);
    toast("Join prompt copied.");
  } catch {
    const field = $("#visitor-join-prompt");
    field.value = text;
    field.hidden = false;
    field.focus();
    field.select();
    toast("Clipboard unavailable. Copy the selected join prompt.");
  }
}
$("#copy-join-prompt").addEventListener("click", copyVisitorJoinPrompt);
function survivalBars(agent) {
  if (![agent.food, agent.water, agent.health].every(Number.isFinite)) return "";
  return `<span class="survival-bars">${["food", "water", "health"].map((stat) => {
    const value = Math.max(0, Math.min(100, agent[stat]));
    return `<span class="survival-stat${value < 25 ? " low" : ""}"><span>${stat} ${Math.round(value)}</span><meter min="0" max="100" low="25" high="60" optimum="100" value="${value}" aria-label="${stat}">${Math.round(value)} / 100</meter></span>`;
  }).join("")}</span>`;
}
function renderCards() {
  const cards = $("#resident-cards");
  const focusedAgent = cards.contains(document.activeElement)
    ? document.activeElement.dataset.agent
    : null;
  cards.innerHTML = state.agents
    .map(
      (a) =>
        `<button class="resident-card${a.id === selected ? " selected" : ""}" data-agent="${esc(a.id)}" aria-pressed="${a.id === selected}">${portrait(a)}<span class="resident-info"><span class="resident-name">${esc(a.name)}${a.provider ? '<i class="provider-dot" title="External provider attached"></i>' : ""}</span><span class="resident-role">${esc(a.role)}</span><span class="resident-activity">${esc(a.activity)}</span>${survivalBars(a)}</span><span class="resident-arrow" aria-hidden="true">›</span></button>`,
    )
    .join("");
  $("#resident-cards")
    .querySelectorAll("[data-agent]")
    .forEach((el) =>
      el.addEventListener("click", () => selectAgent(el.dataset.agent)),
    );
  if (focusedAgent) {
    const replacement = cards.querySelector(`[data-agent="${CSS.escape(focusedAgent)}"]`);
    (replacement || $("#villagers-panel [data-panel-close]"))?.focus({ preventScroll: true });
  }
}
function selectAgent(id) {
  const focusInPanel = $("#villagers-panel")?.contains(document.activeElement);
  selected = id;
  renderSprites();
  renderCards();
  renderDetails();
  openPanel("villagers-panel");
  const panelBody = $("#villagers-panel .panel-body");
  if (panelBody) panelBody.scrollTop = 0;
  const cards = $("#resident-cards"),
    card = cards.querySelector(`[data-agent="${CSS.escape(id)}"]`);
  if (card) {
    cards.scrollLeft += card.getBoundingClientRect().left
      - cards.getBoundingClientRect().left
      - (cards.clientWidth - card.offsetWidth) / 2;
    if (focusInPanel) card.focus({ preventScroll: true });
  }
  const sprite = document.querySelector(`#villagers [data-id="${CSS.escape(id)}"]`);
  if (sprite && !mapOverview) {
    mapViewport.scrollTo({
      left: sprite.offsetLeft - mapViewport.clientWidth / 2,
      top: sprite.offsetTop - mapViewport.clientHeight / 2,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  }
}
function renderGoals(agent) {
  if (!agent.goals?.length) return "";
  return `<p class="detail-label">DAY ${state.day} GOALS</p><div class="goal-list">${agent.goals
    .map(
      (goal) =>
        `<div class="goal-item${goal.completed ? " goal-complete" : ""}"><div class="goal-heading"><span class="goal-title">${esc(goal.title)}</span><span class="goal-status">${goal.completed ? "Done ✓" : `${goal.progress} / ${goal.target}`}</span></div><progress class="goal-progress" value="${goal.progress}" max="${goal.target}" aria-label="${esc(goal.title)}"></progress></div>`,
    )
    .join("")}</div>`;
}
function renderRelationships(agent) {
  const connections = Object.entries(agent.relationships).sort(
    ([first], [second]) => first.localeCompare(second),
  );
  const heading = '<p class="detail-label">FAMILIAR FACES</p>';
  if (!connections.length)
    return `${heading}<p class="empty-note">Friendships begin with a conversation. There are still people to get to know.</p>`;
  return `${heading}<div class="relationship-list">${connections
    .map(([id, fact]) => {
      const other = state.agents.find((a) => a.id === id);
      const name = other?.name || id.charAt(0).toUpperCase() + id.slice(1);
      const sharedFact = typeof fact === "string" ? fact : `${agent.name} and ${name} have shared conversations.`;
      const trust = agent.trust?.[id];
      const trustNote = typeof trust === "number" ? ` · trust ${trust.toFixed(1)}` : "";
      return `<button class="relationship-item" data-friend="${esc(id)}"${other ? "" : " disabled"}><span>${esc(name)}<small>${storyText(sharedFact)}${trustNote}${other ? "" : " · Away from the village"}</small></span>${other ? '<span class="relationship-meta" aria-hidden="true">›</span>' : ""}</button>`;
    })
    .join("")}</div>`;
}
function renderDetails() {
  const a = state.agents.find((a) => a.id === selected);
  if (!a) return;
  renderBrain(a);
  $("#agent-profile").innerHTML =
    `<div class="profile">${portrait(a)}<div><h2>${esc(a.name)}</h2><p>The ${esc(a.role)}</p><div class="profile-status">${a.provider ? esc(a.provider) : "Following their own rhythm"}</div></div></div>`;
  const detail = $("#agent-details"),
    scroll = detail.scrollTop;
  const focusedFriend = detail.contains(document.activeElement)
    ? document.activeElement.dataset.friend
    : null;
  if (tab === "story") {
    const destination =
      world?.places.find((p) => p.id === a.destination)?.name || a.destination;
    detail.innerHTML = `<p class="agent-bio">${esc(a.persona)}</p><p class="detail-label">RIGHT NOW</p><p class="current-activity">${esc(a.activity)}${a.mood ? ` · ${esc(a.mood)}` : ""}</p>${a.thought ? `<p class="agent-thought">${esc(a.thought)}</p>` : ""}<p class="detail-label">HEADING TO</p><p class="current-activity">${esc(destination)}</p>${survivalBars(a)}${renderGoals(a)}${renderRelationships(a)}<div class="detail-stats"><span><strong>${a.memory_count ?? a.memory.length}</strong> memories</span><span><strong>${Object.keys(a.relationships).length}</strong> connections</span><span><strong>${a.work_count}</strong> tasks</span></div>`;
    detail.querySelectorAll("[data-friend]").forEach((button) =>
      button.addEventListener("click", () => selectAgent(button.dataset.friend)),
    );
  } else if (tab === "plan") {
    const current = [...a.plan]
      .reverse()
      .find((p) => p.hour <= state.minute / 60);
    detail.innerHTML = a.plan
      .map(
        (p) =>
          `<div class="plan-item${p === current ? " current" : ""}"><time>${String(p.hour).padStart(2, "0")}:00</time><span>${esc(p.activity)}<small>${esc(world?.places.find((place) => place.id === p.place)?.name || p.place)}</small></span></div>`,
      )
      .join("");
    if (a.cognitive_plan) {
      const plan = a.cognitive_plan;
      detail.insertAdjacentHTML("afterbegin", `<p class="detail-label">${esc(plan.status.toUpperCase())} PLAN</p><p>${esc(plan.goal)}</p>${plan.steps.map((step, index) => `<div class="plan-item${index === plan.index && plan.status === "active" ? " current" : ""}"><time>${step.duration_minutes}m</time><span>${esc(step.action)} · ${esc(step.location)}<small>${esc(step.reason)}</small></span></div>`).join("")}`);
    }
    const appointments = (state.commitments || []).filter((c) => c.terms.participants.includes(a.id));
    if (appointments.length) {
      detail.insertAdjacentHTML("afterbegin", `<p class="detail-label">AGREED MEETINGS</p>${appointments.slice(-8).map((c) => {
        const t = c.terms, minute = t.start_minute % 1440;
        const when = `Day ${Math.floor(t.start_minute / 1440) + 1} · ${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
        return `<div class="plan-item"><time>${esc(when)}</time><span>${esc(t.task.replaceAll("_", " "))} · ${esc(t.place)}<small>${esc(c.status)} · ${t.duration_minutes}m · ${esc(t.participants.join(" & "))}</small></span></div>`;
      }).join("")}`);
    }
    const conversation = (state.conversations || []).findLast((c) => c.participants.includes(a.id));
    if (conversation) {
      detail.insertAdjacentHTML("beforeend", `<p class="detail-label">LATEST CONVERSATION · ${esc(conversation.status)}</p>${conversation.transcript.map((turn) => `<p><strong>${esc(turn.speaker)}</strong>: ${esc(turn.speech)}</p>`).join("")}`);
    }
    if (a.goals?.length)
      detail.insertAdjacentHTML(
        "afterbegin",
        '<p class="empty-note">Unfinished work goals and time with familiar faces can shape this routine. Follow their progress in Story.</p>',
      );
    if (state.weather === "storm" || state.festival)
      detail.insertAdjacentHTML(
        "afterbegin",
        '<p class="empty-note">Today’s routine is taking a little detour for the village event.</p>',
      );
  } else {
    detail.innerHTML = a.memory.length
      ? [...a.memory]
          .reverse()
          .map(
            (m) =>
              `<div class="memory-item"><small>DAY ${m.day} · ${esc(m.time)} · ${esc(m.kind)}</small>${storyText(m.text)}</div>`,
          )
          .join("")
      : '<p class="empty-note">A fresh start. Their first memories are still to come.</p>';
    if ((a.memory_count ?? a.memory.length) > a.memory.length) {
      detail.insertAdjacentHTML("afterbegin", `<p class="empty-note">Showing the latest ${a.memory.length} of ${a.memory_count} memories. Saved stories keep the full history.</p>`);
    }
  }
  detail.scrollTop = scroll;
  if (focusedFriend) {
    const replacement = detail.querySelector(`[data-friend="${CSS.escape(focusedFriend)}"]:not(:disabled)`);
    (replacement || $("#villagers-panel [data-panel-close]"))?.focus({ preventScroll: true });
  }
}

function brainDescription(brain) {
  if (!brain) return "Rule-based routines";
  if (brain.status === "thinking") return `${brain.model} · thinking…`;
  if (brain.status === "error")
    return "Connection needs attention · rules active";
  if (brain.decisions)
    return `${brain.model} · ${brain.decisions} decision${brain.decisions === 1 ? "" : "s"}`;
  return `${brain.model} · waiting`;
}
function renderBrain(agent) {
  const brain = !recording && state.brains?.[agent.id];
  $("#brain-status").textContent = brain
    ? brainDescription(brain)
    : agent.provider
      ? `${agent.provider} · external harness`
      : "Rule-based routines";
  $("#llm-open").textContent = brain ? "LLM settings" : "Connect a brain";
  $("#llm-open").disabled = Boolean(recording) || !operatorUnlocked;
  if ($("#llm-settings").open && brainAgent) updateBrainFeedback();
}
function updateBrainFeedback() {
  const brain = liveState?.brains?.[brainAgent];
  const agent = liveState?.agents.find((a) => a.id === brainAgent);
  const external = Boolean(agent?.provider && !brain);
  $("#llm-feedback").textContent = !agent
    ? "This villager has left the village. Close this panel and choose a resident."
    : external
      ? "An external harness is attached. Disconnect it before connecting an LLM here."
      : brain?.error || brain?.embedding_error || (brain ? brainDescription(brain) : "Ready to connect.");
  $("#llm-feedback").classList.toggle(
    "error",
    Boolean(brain?.error || external || !agent),
  );
  $("#llm-connect").disabled = !agent || external;
  $("#llm-connect").textContent = brain ? "Update connection" : "Connect a brain";
  $("#llm-disconnect").hidden = !brain;
  $("#llm-think").hidden = !brain;
  $("#llm-think").disabled = brain?.status === "thinking";
}
$("#llm-open").addEventListener("click", () => {
  brainAgent = selected;
  const agent = liveState?.agents.find((a) => a.id === brainAgent);
  if (!agent) return;
  const brain = liveState.brains?.[brainAgent];
  $("#llm-title").textContent = `${agent.name}’s brain`;
  $("#llm-endpoint").value = brain?.base_url || "https://api.openai.com/v1";
  $("#llm-provider").value = providerPreset($("#llm-endpoint").value);
  $("#llm-advanced").open = $("#llm-provider").value === "custom";
  $("#llm-model").value = brain?.model || "";
  $("#llm-key").value = "";
  $("#llm-key").placeholder = brain?.has_key
    ? "Key saved for this session"
    : "Your provider’s API key";
  $("#llm-key-note").textContent = brain?.has_key
    ? "Leave blank to keep the current key for this endpoint. Enter a new key when changing endpoints."
    : "Optional for local servers. Keys are kept in server memory for this session.";
  $("#llm-key").closest("label").hidden = $("#llm-provider").value === "ollama";
  $("#llm-frequency").value = brain?.every_steps || 8;
  $("#llm-embedding-model").value = brain?.embedding_model || "";
  updateBrainFeedback();
  $("#llm-settings").showModal();
});
$("#llm-settings").addEventListener("close", () => {
  $("#llm-key").value = "";
  brainAgent = null;
});
$("#llm-settings").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
function providerPreset(endpoint) {
  const base = endpoint.replace(/\/$/, "");
  if (base === "https://api.openai.com/v1") return "openai";
  if (/^http:\/\/(localhost|127\.0\.0\.1):11434\/v1$/.test(base)) return "ollama";
  return "custom";
}
$("#llm-provider").addEventListener("change", (e) => {
  const preset = e.target.value;
  if (preset === "openai") {
    $("#llm-endpoint").value = "https://api.openai.com/v1";
    if ($("#llm-model").value === "llama3.2") $("#llm-model").value = "";
    $("#llm-key-note").textContent = "Your key stays in server memory. It is never saved with the story.";
  } else if (preset === "ollama") {
    $("#llm-endpoint").value = "http://localhost:11434/v1";
    $("#llm-model").value = "llama3.2";
    $("#llm-key").value = "";
    $("#llm-key-note").textContent = "Run Ollama on the village server’s computer with this model installed. No API key needed.";
  }
  $("#llm-advanced").open = preset === "custom";
  $("#llm-key").closest("label").hidden = preset === "ollama";
});
$("#llm-endpoint").addEventListener("input", () => {
  $("#llm-provider").value = providerPreset($("#llm-endpoint").value);
  $("#llm-key").closest("label").hidden = $("#llm-provider").value === "ollama";
});
$("#llm-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const aid = brainAgent;
  $("#llm-connect").disabled = true;
  try {
    const next = await post(`/api/agents/${encodeURIComponent(aid)}/brain`, {
      action: "connect",
      base_url: $("#llm-endpoint").value,
      model: $("#llm-model").value,
      api_key: $("#llm-key").value,
      every_steps: Number($("#llm-frequency").value),
      embedding_model: $("#llm-embedding-model").value,
    });
    $("#llm-key").value = "";
    acceptLive(next);
    if (brainAgent === aid) updateBrainFeedback();
  } catch (error) {
    $("#llm-feedback").textContent = error.message;
    $("#llm-feedback").classList.add("error");
  } finally {
    $("#llm-connect").disabled = false;
  }
});
$("#llm-disconnect").addEventListener("click", async () => {
  try {
    const next = await post(
      `/api/agents/${encodeURIComponent(brainAgent)}/brain`,
      { action: "disconnect" },
    );
    acceptLive(next);
    updateBrainFeedback();
    toast("LLM disconnected. Their memories stay with them.");
  } catch (error) {
    toast(error.message);
  }
});
$("#llm-think").addEventListener("click", async () => {
  try {
    await post(`/api/agents/${encodeURIComponent(brainAgent)}/brain`, {
      action: "think",
    });
  } catch (error) {
    toast(error.message);
  }
});
function journalEntries(events) {
  const seen = new Set();
  return [...events].reverse().filter((event) => {
    if (!["insight", "promise"].includes(event.kind)) return true;
    const key = `${event.day}:${narrativeText(event.text)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
function renderJournal() {
  const journal = $("#journal"), scroll = journal.scrollTop;
  const choresOpen = Boolean(journal.querySelector(".chore-log[open]"));
  const supperOpen = Boolean($("#supper-ledger details[open]"));
  const reflections = state.reflections || state.events.filter((e) => e.kind === "reflection");
  $("#reflections").innerHTML = [...reflections].reverse().map((e) =>
    `<article class="pinned-reflection"><small>✧ MIDNIGHT · DAY ${esc(e.reflected_day ?? e.day)}</small><p>${storyText(e.text)}</p></article>`,
  ).join("") || '<p class="empty-note">At midnight, the village keeps one line from today.</p>';
  const lastSupper = state.suppers?.at(-1);
  $("#supper-ledger").innerHTML = lastSupper
    ? `<details class="supper-history"${supperOpen ? " open" : ""}><summary>Supper ledger · Day ${esc(lastSupper.day)}</summary>${[...state.suppers].reverse().map((supper) => `<p>${esc(supper.text || supper.summary || `Day ${supper.day}: ${supper.fish ?? supper.fish_spent ?? 0} fish, ${supper.herbs ?? supper.herbs_spent ?? 0} herbs`)}</p>`).join("")}</details>`
    : "";
  const visible = journalEntries(state.events).filter((e) => e.kind !== "reflection");
  const chores = visible.filter((e) => e.kind === "work" || e.kind === "work_summary");
  const entries = visible.filter((e) => e.kind !== "work" && e.kind !== "work_summary").slice(0, 35);
  const eventHTML = (e) => `<div class="journal-entry ${e.kind === "conversation" ? "conversation" : ""}"><time title="Day ${esc(e.day)}">${esc(e.time)}</time><p>${storyText(e.text)}</p></div>`;
  const summaries = [...(state.work_summaries || [])].reverse().map((row) => {
    const name = state.agents.find((a) => a.id === row.agent)?.name || row.agent;
    const place = world.places.find((p) => p.id === row.place)?.name || row.place;
    const counts = Object.entries(row.results).map(([result, count]) => `${count} ${result.replaceAll("_", " ")}`).join(", ");
    return `<p class="chore-summary"><small>DAY ${esc(row.day)}</small>${esc(name)} · ${esc(place)}<br>${esc(counts)}</p>`;
  }).join("");
  journal.innerHTML = entries.map(eventHTML).join("")
    + (chores.length || summaries ? `<details class="chore-log"${choresOpen ? " open" : ""}><summary>Work around the village</summary>${summaries || chores.map(eventHTML).join("")}</details>` : "");
  journal.scrollTop = scroll;
}
function renderPlaces() {
  $("#place-labels").innerHTML = world.places
    .map(
      (p) =>
        `<span class="place-label" style="left:${(((p.x + 0.5) * 32) / 1280) * 100}%;top:${(((p.y + 0.5) * 32) / 720) * 100}%">${esc(p.name)}</span>`,
    )
    .join("");
  $("#place-labels").hidden = !showLabels;
}
$("#play").addEventListener("click", () => {
  if (recording) {
    replayPlaying = !replayPlaying;
    if (replayPlaying && replayIndex >= recording.snapshots.length - 1)
      replayIndex = 0;
    clearInterval(replayTimer);
    if (replayPlaying)
      replayTimer = setInterval(() => {
        if (replayIndex >= recording.snapshots.length - 1) {
          replayPlaying = false;
          clearInterval(replayTimer);
        } else replayIndex++;
        renderReplay();
      }, 800);
    renderReplay();
  } else control(state?.paused ? "play" : "pause");
});
$("#step").addEventListener("click", () => {
  if (recording) {
    replayIndex = Math.min(replayIndex + 1, recording.snapshots.length - 1);
    renderReplay();
  } else control("step");
});
$("#speed").addEventListener("change", (e) =>
  control("speed", { interval: Number(e.target.value) }),
);
$("#population").addEventListener("change", (e) =>
  control("population", { count: Number(e.target.value) }),
);
$("#cognition-mode").addEventListener("change", (e) => control("cognition", { mode: e.target.value }));
$("#settings-open").addEventListener("click", () => $("#settings").showModal());
$("#about-open").addEventListener("click", () => {
  $("#settings").close();
  $("#about").showModal();
});
$("dialog#settings").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$("dialog#about").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$("#reset").addEventListener("click", async () => {
  if (
    confirm(
      "Begin a new village session? This clears current memories and the recording.",
    )
  ) {
    await control("reset");
    $("#settings").close();
  }
});
document.querySelectorAll("[data-event]").forEach((button) =>
  button.addEventListener("click", async () => {
    try {
      await post("/api/events", { kind: button.dataset.event });
    } catch (error) {
      toast(error.message);
    }
  }),
);
document.querySelectorAll("[data-tab]").forEach((button) =>
  button.addEventListener("click", () => {
    tab = button.dataset.tab;
    document
      .querySelectorAll("[data-tab]")
      .forEach((b) => b.setAttribute("aria-selected", String(b === button)));
    $("#agent-details").scrollTop = 0;
    if (state) renderDetails();
  }),
);
$("#labels").addEventListener("click", () => {
  showLabels = !showLabels;
  $("#place-labels").hidden = !showLabels;
  $("#labels").setAttribute("aria-pressed", String(showLabels));
});
const mapViewport = $("#scene-viewport"),
  mapCanvas = $("#scene-inner");
let mapOverview = false,
  mapZoom = 1,
  mapDrag = null;
function resizeMap() {
  // Overview reserves room around the map; Fill screen runs behind the HUD.
  mapViewport.classList.toggle("overview", mapOverview);
  const width = mapViewport.clientWidth,
    height = mapViewport.clientHeight;
  const oldWidth = mapCanvas.clientWidth || width;
  const centerX = mapOverview || oldWidth <= width ? 0.5 : (mapViewport.scrollLeft + width / 2) / oldWidth;
  const centerY = mapOverview || oldWidth * 9 / 16 <= height ? 0.5 : (mapViewport.scrollTop + height / 2) / (oldWidth * 9 / 16);
  const canvasWidth = Math.max(1, mapOverview
    ? Math.min(width, height * 16 / 9)
    : Math.max(width, height * 16 / 9) * mapZoom);
  mapCanvas.style.setProperty("--map-width", `${canvasWidth}px`);
  $("#fill-screen").setAttribute("aria-pressed", String(!mapOverview && mapZoom === 1));
  $("#overview").setAttribute("aria-pressed", String(mapOverview));
  $("#map-hint").textContent = mapOverview
    ? "Whole village · Zoom to explore"
    : "Drag or scroll to explore";
  $("#zoom").disabled = mapZoom >= 3;
  $("#zoom-out").disabled = mapOverview;
  $("#map-scale").textContent = mapOverview ? "Fit" : `${Math.round(mapZoom * 100)}%`;
  mapViewport.scrollLeft = centerX * canvasWidth - width / 2;
  mapViewport.scrollTop = centerY * canvasWidth * 9 / 16 - height / 2;
}
$("#fill-screen").addEventListener("click", () => {
  mapOverview = false;
  mapZoom = 1;
  resizeMap();
});
$("#overview").addEventListener("click", () => {
  mapOverview = true;
  mapZoom = 1;
  resizeMap();
});
$("#zoom").addEventListener("click", () => {
  mapZoom = Math.min(3, mapZoom + 0.5);
  mapOverview = false;
  resizeMap();
});
$("#zoom-out").addEventListener("click", () => {
  if (mapZoom > 1) mapZoom = Math.max(1, mapZoom - 0.5);
  else mapOverview = true;
  resizeMap();
});
mapViewport.addEventListener("pointerdown", (event) => {
  if (
    mapOverview || event.pointerType !== "mouse" || event.button !== 0 ||
    event.target.closest("button")
  ) return;
  mapDrag = {
    id: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    left: mapViewport.scrollLeft,
    top: mapViewport.scrollTop,
  };
  mapViewport.setPointerCapture(event.pointerId);
  mapViewport.classList.add("dragging");
  mapViewport.focus({ preventScroll: true });
  event.preventDefault();
});
mapViewport.addEventListener("pointermove", (event) => {
  if (!mapDrag || mapDrag.id !== event.pointerId) return;
  mapViewport.scrollLeft = mapDrag.left + mapDrag.x - event.clientX;
  mapViewport.scrollTop = mapDrag.top + mapDrag.y - event.clientY;
});
function stopMapDrag() {
  mapDrag = null;
  mapViewport.classList.remove("dragging");
}
mapViewport.addEventListener("pointerup", stopMapDrag);
mapViewport.addEventListener("pointercancel", stopMapDrag);
mapViewport.addEventListener("lostpointercapture", stopMapDrag);
new ResizeObserver(resizeMap).observe(mapViewport);
resizeMap();
$("#export").addEventListener("click", async () => {
  try {
    const blob = recording
      ? new Blob([JSON.stringify(recording)], { type: "application/json" })
      : await fetch("/api/recording").then((r) => {
          if (!r.ok) throw new Error("Could not save the complete recording. Check the server's save status.");
          return r.blob();
        });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `lantern-bay-step-${recording?.snapshots.at(-1).step ?? liveState.step}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Your village story has been saved.");
  } catch (error) {
    toast(error.message);
  }
});
function validCognitivePlan(plan) {
  return plan == null || (
    ["active", "completed", "invalidated"].includes(plan.status) &&
    typeof plan.goal === "string" && plan.goal.length <= 200 &&
    Array.isArray(plan.steps) && plan.steps.length > 0 && plan.steps.length <= 8 &&
    Number.isInteger(plan.index) && plan.index >= 0 && plan.index <= plan.steps.length &&
    plan.steps.every((step) => step &&
      ["work", "visit", "rest", "routine", "drink", "eat", "collect_water"].includes(step.action) &&
      typeof step.location === "string" && typeof step.reason === "string" &&
      Number.isInteger(step.duration_minutes) && step.duration_minutes >= 15 &&
      step.duration_minutes <= 180 && step.duration_minutes % 15 === 0)
  );
}
function validRecording(data) {
  if (
    data?.schema_version !== 1 ||
    !Array.isArray(data.snapshots) ||
    !data.snapshots.length ||
    !Array.isArray(data.world?.places)
  )
    return false;
  if (
    !data.world.places.every(
      (p) =>
        typeof p.id === "string" &&
        typeof p.name === "string" &&
        Number.isFinite(p.x) &&
        Number.isFinite(p.y),
    )
  )
    return false;
  return true;
}
function validSurvival(s) {
  if (s.villager_count !== undefined && s.villager_count !== s.agents?.length) return false;
  if (s.water_sources !== undefined && (!s.water_sources ||
      !Object.values(s.water_sources).every((source) => source &&
        Number.isInteger(source.supply) && source.supply >= 0 &&
        Number.isInteger(source.fouled_until) && source.fouled_until >= 0))) return false;
  if (s.collection_centers !== undefined && (!s.collection_centers ||
      !["food", "water"].every((key) => {
        const center = s.collection_centers[key];
        return center && typeof center.place === "string" &&
          Number.isFinite(center.level) && center.level >= 0 &&
          Number.isFinite(center.daily_need) && center.daily_need > 0 && typeof center.low === "boolean";
      }))) return false;
  return Array.isArray(s.agents) && s.agents.every((agent) =>
    ["food", "water", "health"].every((key) => agent[key] === undefined ||
      (Number.isFinite(agent[key]) && agent[key] >= 0 && agent[key] <= 100)) &&
    (agent.collapsed_until === undefined || (Number.isInteger(agent.collapsed_until) && agent.collapsed_until >= 0)));
}
function validSnapshot(s) {
  return (
      validSurvival(s) &&
      Number.isInteger(s.step) &&
      Number.isInteger(s.day) &&
      Number.isFinite(s.minute) &&
      typeof s.time === "string" &&
      (s.commitments === undefined || (Array.isArray(s.commitments) && s.commitments.every((c) =>
        c && ["scheduled", "completed", "missed", "cancelled"].includes(c.status) &&
        c.terms && Array.isArray(c.terms.participants) && c.terms.participants.length === 2 &&
        c.terms.participants.every((id) => typeof id === "string") &&
        typeof c.terms.task === "string" && typeof c.terms.place === "string" &&
        Number.isInteger(c.terms.start_minute) && c.terms.start_minute >= 0 &&
        Number.isInteger(c.terms.duration_minutes) && c.terms.duration_minutes > 0))) &&
      (s.conversations === undefined || (Array.isArray(s.conversations) && s.conversations.every((c) =>
        c && typeof c.status === "string" && Array.isArray(c.participants) &&
        c.participants.every((id) => typeof id === "string") &&
        Array.isArray(c.transcript) && c.transcript.length <= 6 && c.transcript.every((turn) =>
          turn && typeof turn.speaker === "string" && typeof turn.speech === "string" && turn.speech.length <= 240)))) &&
      Array.isArray(s.events) &&
      Array.isArray(s.agents) &&
      s.agents.length > 0 &&
      s.agents.length <= 8 &&
      s.events.every(
        (m) => typeof m.text === "string" && typeof m.time === "string",
      ) &&
      s.agents.every(
        (a) =>
          typeof a.id === "string" &&
          /^[a-z][a-z0-9_-]{0,30}$/.test(a.id) &&
          typeof a.name === "string" &&
          typeof a.activity === "string" &&
          Number.isFinite(a.x) &&
          Number.isFinite(a.y) &&
          a.x >= 0 &&
          a.x < 40 &&
          a.y >= 0 &&
          a.y < 23 &&
          Array.isArray(a.memory) &&
          a.memory.every(
            (m) => typeof m.text === "string" && typeof m.time === "string",
          ) &&
          Array.isArray(a.plan) &&
          a.plan.every(
            (p) =>
              Number.isFinite(p.hour) &&
              typeof p.place === "string" &&
              typeof p.activity === "string",
          ) &&
          a.relationships &&
          typeof a.relationships === "object" &&
          !Array.isArray(a.relationships) &&
          Object.values(a.relationships).every(
            (fact) => typeof fact === "string" || (Number.isInteger(fact) && fact >= 0),
          ) &&
          validCognitivePlan(a.cognitive_plan) &&
          (a.goals === undefined ||
            (Array.isArray(a.goals) &&
              a.goals.every(
                (goal) =>
                  goal &&
                  typeof goal.title === "string" &&
                  Number.isInteger(goal.target) &&
                  goal.target > 0 &&
                  Number.isInteger(goal.progress) &&
                  goal.progress >= 0 &&
                  goal.progress <= goal.target &&
                  typeof goal.completed === "boolean",
              ))),
      )
  );
}
async function snapshotsLookValid(snapshots) {
  const chunk = 200;
  for (let index = 0; index < snapshots.length; index += chunk) {
    const end = Math.min(snapshots.length, index + chunk);
    for (let cursor = index; cursor < end; cursor += 1) {
      if (!validSnapshot(snapshots[cursor])) return false;
    }
    if (end < snapshots.length) {
      toast(`Checking the story… ${end} / ${snapshots.length} frames`);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
  return true;
}
function confirmLargeStory(file, snapshots) {
  const bytes = 8 * 1024 * 1024;
  const frames = 2000;
  if (file.size < bytes && snapshots.length < frames) return true;
  const size = file.size >= bytes ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : null;
  const count = snapshots.length >= frames ? `${snapshots.length} frames` : null;
  const detail = [size, count].filter(Boolean).join(" and ");
  return confirm(
    `This story is ${detail}. Opening it keeps the whole replay in browser memory and can make the page slow. Open it anyway?`,
  );
}
$("#import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size >= 8 * 1024 * 1024 && !confirmLargeStory(file, [])) {
      return;
    }
    const data = JSON.parse(await file.text());
    if (!validRecording(data))
      throw new Error("That file is not a supported Village Sim recording.");
    if (file.size < 8 * 1024 * 1024 && !confirmLargeStory(file, data.snapshots)) return;
    if (!(await snapshotsLookValid(data.snapshots)))
      throw new Error("That file is not a supported Village Sim recording.");
    recording = data;
    world = data.world;
    replayIndex = 0;
    replayPlaying = false;
    clearInterval(replayTimer);
    $("#replay-bar").hidden = false;
    $("#resume-story").disabled = !data.session;
    $("#resume-story").title = data.session
      ? "Continue from the last saved moment" : "Older recordings can only be replayed";
    $("#replay-position").max = data.snapshots.length - 1;
    $("#settings").close();
    closePanel();
    renderPlaces();
    renderReplay();
    const large = file.size >= 8 * 1024 * 1024 || data.snapshots.length >= 2000;
    toast(large
      ? "Large story opened. Playback keeps every frame in browser memory."
      : data.session
        ? "Story opened. Replay it or resume from its last saved moment."
        : "Recording opened. This older recording supports replay only.");
  } catch (error) {
    toast(
      error instanceof SyntaxError
        ? "That file is not valid JSON."
        : error.message,
    );
  } finally {
    e.target.value = "";
  }
});
function renderReplay() {
  $("#replay-position").value = replayIndex;
  $("#replay-count").textContent =
    `${replayIndex + 1} / ${recording.snapshots.length}`;
  render(recording.snapshots[replayIndex]);
}
$("#replay-position").addEventListener("input", (e) => {
  replayIndex = Number(e.target.value);
  renderReplay();
});
$("#resume-story").addEventListener("click", async () => {
  if (!recording?.session) return;
  if (!confirm("Resume this saved story? It replaces the live village and pauses at the last saved moment. Save the live story first if you want to keep it.")) return;
  $("#resume-story").disabled = true;
  try {
    const next = await post("/api/session/resume", { session: recording.session });
    clearInterval(replayTimer);
    replayPlaying = false;
    recording = null;
    world = await fetch("/api/world").then((r) => r.json());
    $("#replay-bar").hidden = true;
    renderPlaces();
    acceptLive(next);
    $("#play").focus({ preventScroll: true });
    toast("Story resumed and paused. Press play to continue; reconnect any model brains.");
  } catch (error) {
    toast(error.message);
    $("#resume-story").disabled = !recording?.session;
  }
});
$("#return-live").addEventListener("click", async () => {
  clearInterval(replayTimer);
  replayPlaying = false;
  recording = null;
  renderOperatorControls();
  $("#replay-bar").hidden = true;
  $("#play").focus({ preventScroll: true });
  try {
    world = await fetch("/api/world").then((r) => r.json());
    renderPlaces();
    if (liveState) render(liveState);
  } catch {
    toast("The live village is unavailable. Reconnecting…");
  }
});

const canvas = $("#atmosphere"),
  ctx = canvas.getContext("2d");
canvas.width = 1280;
canvas.height = 720;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let lastFrame = 0;
function atmosphere(now) {
  requestAnimationFrame(atmosphere);
  if (now - lastFrame < 40 || document.hidden) return;
  lastFrame = now;
  ctx.clearRect(0, 0, 1280, 720);
  if (reducedMotion) return;
  const seconds = now / 1000;
  if (state?.weather === "storm") {
    ctx.fillStyle = "#0b263f44";
    ctx.fillRect(0, 0, 1280, 720);
    ctx.strokeStyle = "#abc8d65c";
    ctx.lineWidth = 1;
    for (let i = 0; i < 80; i++) {
      const x = (i * 127.9 - ((seconds * 140) % 1400) + 1400) % 1400,
        y = (i * 93.3 + seconds * 430) % 800;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - 8, y + 21);
      ctx.stroke();
    }
  } else {
    const night = state && (state.minute < 360 || state.minute >= 1080);
    for (let i = 0; night && i < 17; i++) {
      const x = 100 + ((i * 79) % 770) + Math.sin(seconds * 0.35 + i) * 17;
      const y = 230 + ((i * 97) % 390) + Math.cos(seconds * 0.4 + i) * 12;
      const alpha = 0.15 + (Math.sin(seconds * 1.2 + i) + 1) * 0.2;
      ctx.beginPath();
      ctx.fillStyle = `rgba(247,211,123,${alpha})`;
      ctx.arc(x, y, 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
    if (state?.festival && night) {
      for (let i = 0; i < 10; i++) {
        const x = 260 + i * 65 + Math.sin(seconds + i) * 8,
          y = 650 - ((seconds * 20 + i * 70) % 650);
        ctx.shadowColor = "#f4b85e";
        ctx.shadowBlur = 20;
        ctx.fillStyle = "#e8ad5ddd";
        ctx.fillRect(x, y, 7, 10);
      }
      ctx.shadowBlur = 0;
    }
  }
}
requestAnimationFrame(atmosphere);
async function start() {
  try {
    const response = await fetch("/api/world");
    if (!response.ok) throw new Error("The village could not load.");
    world = await response.json();
    renderPlaces();
    const initial = await fetch("/api/state").then((r) => r.json());
    acceptLive(initial);
    const stream = new EventSource("/api/stream");
    stream.onopen = () => {
      connected = true;
      hasConnected = true;
      connectionLabel();
    };
    stream.onmessage = (event) => {
      acceptLive(JSON.parse(event.data));
    };
    stream.onerror = () => {
      connected = false;
      connectionLabel();
    };
  } catch (error) {
    toast(error.message);
    connected = false;
    connectionLabel();
    setTimeout(start, 3000);
  }
}
start();
