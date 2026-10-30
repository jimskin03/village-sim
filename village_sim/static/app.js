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
  toastTimer,
  showLabels = false;
let brainAgent = null;
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
async function post(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();
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
function connectionLabel() {
  $(".connection").classList.toggle("offline", !connected);
  $("#connection-label").textContent = connected
    ? "World is running"
    : "Reconnecting…";
  if (connected && liveState?.paused)
    $("#connection-label").textContent = "World is paused";
}
function acceptLive(next) {
  if (liveState && next.revision < liveState.revision) return;
  if (next.persistence_error && next.persistence_error !== liveState?.persistence_error)
    toast(next.persistence_error);
  liveState = next;
  $("#persistence-status").textContent = next.persistence_error || "The village is saved after every step.";
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
  connectionLabel();
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
  if (state.visitor) markers.push({ place: "guest_home", icon: "✧",
    text: `${state.visitor.name || "Festival guest"} · visiting`, note: "A festival visitor is staying at the guest cottage" });
  container.innerHTML = markers.map((marker) => {
    const place = world.places.find((p) => p.id === marker.place);
    if (!place) return "";
    return `<span class="state-marker${marker.short ? " shortage" : ""}" style="${placePosition(place)}" title="${esc(marker.note)}"><span aria-hidden="true">${marker.icon}</span> ${esc(marker.text)}${marker.short ? '<span class="sr-only"> · needs attention</span>' : ""}</span>`;
  }).join("");
  if (state.visitor) {
    const place = world.places.find((p) => p.id === "guest_home");
    if (place) container.insertAdjacentHTML("beforeend", `<span class="festival-visitor" style="${placePosition(place)};--agent-color:#dfc99b" role="img" aria-label="${esc(state.visitor.name || "A visitor")} at the guest cottage"><span class="sprite-shadow"></span><span class="sprite-legs"></span><span class="sprite-body"></span><span class="sprite-head"></span></span>`);
  }
}
function renderSprites() {
  const container = $("#villagers");
  container.querySelectorAll(".sprite").forEach((el) => {
    if (!state.agents.some((a) => a.id === el.dataset.id)) el.remove();
  });
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
    el.className = `sprite${a.id === selected ? " selected" : ""}${a.activity.startsWith("walking") ? " walking" : ""}`;
    el.style.left = `${(((a.x + 0.5) * 32) / 1280) * 100}%`;
    el.style.top = `${(((a.y + 0.5) * 32) / 720) * 100}%`;
    el.style.setProperty("--agent-color", color(a.color));
    el.setAttribute("aria-label", `${a.name}, ${a.activity}. Select villager.`);
    el.setAttribute("aria-pressed", String(a.id === selected));
    el.querySelector(".sprite-name").textContent = a.name;
    const brain = !recording && state.brains?.[a.id];
    const brainLabel = brain?.status === "thinking" ? "thinking / rules"
      : brain?.status === "ready" ? "model" : "rules";
    const badge = el.querySelector(".sprite-brain");
    badge.textContent = brainLabel;
    badge.classList.toggle("thinking", brain?.status === "thinking");
    const speech = el.querySelector(".speech"), thought = el.querySelector(".thought");
    speech.hidden = !a.speech;
    speech.textContent = a.speech || "";
    thought.hidden = !a.thought;
    thought.textContent = a.thought ? `Thinking: ${a.thought}` : "";
    el.querySelector(".sprite-message").hidden = !(a.speech || a.thought);
    el.setAttribute("aria-label", `${a.name}, ${a.activity}. ${brainLabel}.${a.thought ? ` Thinking: ${a.thought}` : ""} Select villager.`);
    el.style.zIndex = a.speech || a.thought
      ? 20
      : a.id === selected
        ? 10
        : 2 + Math.floor(a.y / 4);
  });
}
function renderCards() {
  const cards = $("#resident-cards");
  const focusedAgent = cards.contains(document.activeElement)
    ? document.activeElement.dataset.agent
    : null;
  cards.innerHTML = state.agents
    .map(
      (a) =>
        `<button class="resident-card${a.id === selected ? " selected" : ""}" data-agent="${esc(a.id)}" aria-pressed="${a.id === selected}">${portrait(a)}<span class="resident-info"><span class="resident-name">${esc(a.name)}${a.provider ? '<i class="provider-dot" title="External provider attached"></i>' : ""}</span><span class="resident-role">${esc(a.role)}</span><span class="resident-activity">${esc(a.activity)}</span></span><span class="resident-arrow" aria-hidden="true">›</span></button>`,
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
      return `<button class="relationship-item" data-friend="${esc(id)}"${other ? "" : " disabled"}><span>${esc(name)}<small>${esc(sharedFact)}${other ? "" : " · Away from the village"}</small></span>${other ? '<span class="relationship-meta" aria-hidden="true">›</span>' : ""}</button>`;
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
    detail.innerHTML = `<p class="agent-bio">${esc(a.persona)}</p><p class="detail-label">RIGHT NOW</p><p class="current-activity">${esc(a.activity)}${a.mood ? ` · ${esc(a.mood)}` : ""}</p>${a.thought ? `<p class="agent-thought">${esc(a.thought)}</p>` : ""}<p class="detail-label">HEADING TO</p><p class="current-activity">${esc(destination)}</p>${renderGoals(a)}${renderRelationships(a)}<div class="detail-stats"><span><strong>${a.memory.length}</strong> memories</span><span><strong>${Object.keys(a.relationships).length}</strong> connections</span><span><strong>${a.work_count}</strong> tasks</span></div>`;
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
              `<div class="memory-item"><small>DAY ${m.day} · ${esc(m.time)} · ${esc(m.kind)}</small>${esc(m.text)}</div>`,
          )
          .join("")
      : '<p class="empty-note">A fresh start. Their first memories are still to come.</p>';
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
  $("#llm-open").disabled = Boolean(recording);
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
      : brain?.error || (brain ? brainDescription(brain) : "Ready to connect.");
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
function renderJournal() {
  const journal = $("#journal"), scroll = journal.scrollTop;
  const choresOpen = Boolean(journal.querySelector(".chore-log[open]"));
  const supperOpen = Boolean($("#supper-ledger details[open]"));
  const reflections = state.reflections || state.events.filter((e) => e.kind === "reflection");
  $("#reflections").innerHTML = [...reflections].reverse().map((e) =>
    `<article class="pinned-reflection"><small>✧ MIDNIGHT · DAY ${esc(e.reflected_day ?? e.day)}</small><p>${esc(e.text)}</p></article>`,
  ).join("") || '<p class="empty-note">At midnight, the village keeps one line from today.</p>';
  const lastSupper = state.suppers?.at(-1);
  $("#supper-ledger").innerHTML = lastSupper
    ? `<details class="supper-history"${supperOpen ? " open" : ""}><summary>Supper ledger · Day ${esc(lastSupper.day)}</summary>${[...state.suppers].reverse().map((supper) => `<p>${esc(supper.text || supper.summary || `Day ${supper.day}: ${supper.fish ?? supper.fish_spent ?? 0} fish, ${supper.herbs ?? supper.herbs_spent ?? 0} herbs`)}</p>`).join("")}</details>`
    : "";
  const visible = [...state.events].reverse().filter((e) => e.kind !== "reflection");
  const chores = visible.filter((e) => e.kind === "work" || e.kind === "work_summary");
  const entries = visible.filter((e) => e.kind !== "work" && e.kind !== "work_summary").slice(0, 35);
  const eventHTML = (e) => `<div class="journal-entry ${e.kind === "conversation" ? "conversation" : ""}"><time title="Day ${esc(e.day)}">${esc(e.time)}</time><p>${esc(e.text)}</p></div>`;
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
  mapZoom = false,
  mapDrag = null;
function resizeMap() {
  const width = mapViewport.clientWidth,
    height = mapViewport.clientHeight;
  const enlargedWidth = Math.max(
    window.innerWidth > 820 ? 2048 : 1344,
    width,
    height * 16 / 9,
  );
  const canvasWidth = mapOverview
    ? Math.min(width, height * 16 / 9)
    : enlargedWidth * (mapZoom ? 1.4 : 1);
  mapCanvas.style.setProperty("--map-width", `${canvasWidth}px`);
  mapViewport.classList.toggle("overview", mapOverview);
  $("#overview").setAttribute("aria-pressed", String(mapOverview));
  $("#map-hint").textContent = mapOverview
    ? "Whole village · Zoom to explore"
    : "Drag or scroll to explore";
  $("#zoom").setAttribute("aria-pressed", String(mapZoom));
  $("#zoom").setAttribute(
    "aria-label",
    mapZoom ? "Zoom out one level" : "Zoom into the village",
  );
  $("#zoom").title = mapZoom ? "Zoom out one level" : "Zoom into the village";
  $("#zoom").textContent = mapZoom ? "−" : "＋";
  // Keep the village centered when changing views or resizing the browser.
  mapViewport.scrollLeft = (canvasWidth - width) / 2;
  mapViewport.scrollTop = (canvasWidth * 9 / 16 - height) * 0.55;
}
$("#overview").addEventListener("click", () => {
  mapOverview = !mapOverview;
  mapZoom = false;
  resizeMap();
});
$("#zoom").addEventListener("click", () => {
  mapZoom = mapOverview ? false : !mapZoom;
  mapOverview = false;
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
    const data =
      recording ||
      (await fetch("/api/recording").then((r) => {
        if (!r.ok) throw new Error("Could not save the recording.");
        return r.json();
      }));
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `lantern-bay-step-${data.snapshots.at(-1).step}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Your village story has been saved.");
  } catch (error) {
    toast(error.message);
  }
});
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
  return data.snapshots.every(
    (s) =>
      Number.isInteger(s.step) &&
      Number.isInteger(s.day) &&
      Number.isFinite(s.minute) &&
      typeof s.time === "string" &&
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
      ),
  );
}
$("#import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 50 * 1024 * 1024)
      throw new Error("Choose a recording smaller than 50 MB.");
    const data = JSON.parse(await file.text());
    if (!validRecording(data))
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
    toast(data.session ? "Story opened. Replay it or resume from its last saved moment." : "Recording opened. This older recording supports replay only.");
  } catch (error) {
    toast(
      error instanceof SyntaxError
        ? "That file is not valid JSON."
        : error.message,
    );
  }
  e.target.value = "";
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
