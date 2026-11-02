"use strict";
(() => {
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(
  /[&<>"']/g,
  (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
);
const number = (value) => Number(value || 0).toLocaleString("en-GB");
const label = (kind) => String(kind).replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

let visible = false;
let snapshot = null;
let context = {};
let summary = null;
let agent = "";
let kind = "";
let branch = "";
let search = "";
let selectedEvent = null;
let nextBefore = null;
let events = [];
let zoom = 1;
let pan = { x: 0, y: 0 };
let requestController = null;
let searchTimer = null;
let storyKey = "";

const BRANCHES = [
  { id: "work", name: "Work", mark: "⚒", color: "#c4a36a", kinds: ["work", "goal", "work_summary"] },
  { id: "talk", name: "Conversations", mark: "✎", color: "#7faf9a", kinds: ["conversation", "promise", "report"] },
  { id: "plans", name: "Plans", mark: "✶", color: "#8aa0c8", kinds: ["plan", "insight", "lesson", "reflection"] },
  { id: "world", name: "World", mark: "☾", color: "#6f8fbf", kinds: ["village", "weather", "festival", "supper", "survival", "collapse", "governance"] },
  { id: "visitors", name: "Visitors", mark: "◈", color: "#c48b7a", kinds: ["visitor", "visitor_speech", "visitor_influence", "visitor_work", "visitor_effect", "visitor_witness"] },
  { id: "life", name: "Everyday life", mark: "·", color: "#b7a48a", kinds: ["arrival", "activity", "trust"] },
];
const filtered = () => Boolean(agent || kind || branch || search);
const resetFilters = () => {
  agent = "";
  kind = "";
  branch = "";
  search = "";
  $("#analytics-search").value = "";
};

function setStatus(message, error = false) {
  $("#analytics-status").textContent = message;
  $("#analytics-status").classList.toggle("analytics-error", error);
}

function duration(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours <= 0) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function kindsForRequest() {
  if (kind) return [kind];
  if (branch) return summary?.branches.find((item) => item.id === branch)?.kinds || [];
  return [];
}

function localReport(source, query) {
  const residents = (source.agents || []).map((person) => ({
    ...person,
    event_count: (source.events || []).filter((event) => event.agents.includes(person.id)).length,
    active: true,
    by_kind: {},
  }));
  const names = Object.fromEntries(residents.map((person) => [person.id, person.name]));
  const needle = String(query.search || "").toLowerCase();
  const matched = [];
  const byKind = {};
  for (const event of source.events || []) {
    byKind[event.kind] = (byKind[event.kind] || 0) + 1;
    for (const id of event.agents) {
      const person = residents.find((item) => item.id === id);
      if (person) person.by_kind[event.kind] = (person.by_kind[event.kind] || 0) + 1;
    }
    if (query.agent && !event.agents.includes(query.agent)) continue;
    if (query.kinds.length && !query.kinds.includes(event.kind)) continue;
    if (needle) {
      const haystack = [event.text, event.kind, ...event.agents.map((id) => names[id] || id)].join(" ").toLowerCase();
      if (!haystack.includes(needle)) continue;
    }
    matched.push(event);
  }
  matched.reverse();
  const page = query.before == null ? matched : matched.filter((event) => event.id < query.before);
  const shown = page.slice(0, query.limit);
  const known = new Set(BRANCHES.flatMap((item) => item.kinds));
  const branches = BRANCHES.map((item) => ({
    ...item,
    count: item.kinds.reduce((total, itemKind) => total + (byKind[itemKind] || 0), 0),
  }));
  const other = Object.keys(byKind).filter((item) => !known.has(item));
  if (other.length) {
    branches.push({
      id: "other", name: "Other", mark: "+", color: "#8d8a82", kinds: other,
      count: other.reduce((total, item) => total + byKind[item], 0),
    });
  }
  return {
    event_count: source.event_count ?? matched.length,
    matching_events: matched.length,
    next_before: page.length > shown.length ? shown.at(-1).id : null,
    by_kind: byKind,
    events: shown,
    residents,
    branches,
    tasks_completed: residents.reduce((total, person) => total + (person.work_count || 0), 0),
    elapsed_minutes: (source.step || 0) * (context.world?.minutes_per_step || 15),
    history_complete: (source.events || []).length >= (source.event_count ?? (source.events || []).length),
    session_id: source.session_id,
    revision: source.revision,
  };
}

async function refresh(more = false) {
  if (!visible || !snapshot) return;
  const query = {
    agent,
    kinds: kindsForRequest(),
    search,
    before: more ? nextBefore : null,
    limit: 40,
  };
  const key = context.recording
    ? `replay:${context.replayIndex}`
    : `${snapshot.session_id}:${snapshot.revision}`;
  if (!more && key !== storyKey && (context.recording ? storyKey && !storyKey.startsWith("replay:") : storyKey && storyKey.split(":")[0] !== String(snapshot.session_id))) {
    resetFilters();
    query.agent = "";
    query.kinds = [];
    query.search = "";
    selectedEvent = null;
  }
  if (context.recording) {
    const data = localReport(snapshot, query);
    applyReport(data, more);
    if (!data.history_complete) {
      setStatus("This older story keeps a recent window of events, not the full ledger.");
    }
    return;
  }
  $("#analytics-more").disabled = true;
  requestController?.abort();
  requestController = new AbortController();
  const params = new URLSearchParams();
  if (query.agent) params.set("agent", query.agent);
  for (const item of query.kinds) params.append("kind", item);
  if (query.search) params.set("search", query.search);
  if (query.before != null) params.set("before", String(query.before));
  params.set("limit", String(query.limit));
  try {
    const response = await fetch(`/api/analytics?${params}`, { signal: requestController.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load session analytics.");
    if (snapshot.session_id && data.session_id !== snapshot.session_id) return;
    applyReport(data, more);
    setStatus(data.history_complete ? "" : "This story does not include the full event ledger.");
  } catch (error) {
    if (error.name === "AbortError") return;
    setStatus(`${error.message} Open Analytics again to retry.`, true);
  } finally {
    $("#analytics-more").disabled = false;
  }
}

function applyReport(data, more) {
  summary = data;
  nextBefore = data.next_before;
  events = more ? events.concat(data.events) : data.events;
  storyKey = context.recording
    ? `replay:${context.replayIndex}`
    : `${data.session_id}:${snapshot.revision}`;
  renderStats(data);
  renderFilters(data);
  renderNetwork(data);
  renderEvents();
  renderSelection(data);
}

function renderStats(data) {
  $("#analytics-stats").innerHTML = [
    ["Events", number(data.event_count), "in this session"],
    ["Villagers", number(data.residents.length), "including those away"],
    ["Tasks", number(data.tasks_completed), "completed"],
    ["Village time", duration(data.elapsed_minutes), "since the lanterns were lit"],
  ].map(([title, value, caption]) => `<div class="analytics-stat"><span>${title}</span><strong>${value}</strong><small>${caption}</small></div>`).join("");
  $("#analytics-mode").textContent = context.recording ? "Recorded story" : snapshot.paused ? "Session paused" : "Live session";
  $("#analytics-mode").classList.toggle("is-paused", Boolean(context.recording || snapshot.paused));
}

function renderFilters(data) {
  const agentOptions = `<option value="">All villagers</option>` + data.residents.map((person) =>
    `<option value="${escapeHtml(person.id)}">${escapeHtml(person.name)}${person.active ? "" : " · away"}</option>`).join("");
  if ($("#analytics-agent").innerHTML !== agentOptions) $("#analytics-agent").innerHTML = agentOptions;
  $("#analytics-agent").value = agent;
  const kindOptions = `<option value="">All event types</option>` + Object.keys(data.by_kind).sort().map((item) =>
    `<option value="${escapeHtml(item)}">${escapeHtml(label(item))}</option>`).join("");
  if ($("#analytics-kind").innerHTML !== kindOptions) $("#analytics-kind").innerHTML = kindOptions;
  $("#analytics-kind").value = kind;
  $("#analytics-clear").hidden = !filtered();
  $("#analytics-filter-note span").textContent = [
    data.residents.find((person) => person.id === agent)?.name,
    branch ? data.branches.find((item) => item.id === branch)?.name : (kind ? label(kind) : ""),
    search ? `“${search}”` : "",
  ].filter(Boolean).join(" · ") || "All session events";
  $("#analytics-event-count").textContent = number(data.matching_events);
}

function renderNetwork(data) {
  const mobile = window.matchMedia("(max-width: 700px)").matches;
  const width = mobile ? 640 : 1100;
  const height = mobile ? 480 : 520;
  const cx = width / 2;
  const cy = height / 2 - 8;
  $("#analytics-network").setAttribute("viewBox", `0 0 ${width} ${height}`);
  const residents = data.residents.filter((person) => person.event_count || person.active);
  const dots = [];
  residents.forEach((person, index) => {
    const angle = Math.PI / 2 + ((index + 0.5) / Math.max(residents.length, 1)) * Math.PI;
    const radius = mobile ? 150 : 220;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius * 0.78;
    const active = agent === person.id;
    dots.push(node(x, y, mobile ? 16 : 18, person.color, person.name,
      `${number(person.event_count)} events${mobile ? "" : ` · ${person.active ? person.activity : "away"}`}`,
      person.name.charAt(0), `data-analytics-agent="${escapeHtml(person.id)}"`, active, agent && agent !== person.id));
    person.x = x;
    person.y = y;
  });
  const links = [];
  data.branches.forEach((item, index) => {
    const angle = -Math.PI / 2 + ((index + 0.5) / Math.max(data.branches.length, 1)) * Math.PI;
    const radius = mobile ? 168 : 230;
    item.x = cx + Math.cos(angle) * radius;
    item.y = cy + Math.sin(angle) * radius * 0.72;
    const active = branch === item.id;
    dots.push(node(item.x, item.y, mobile ? 16 : 20, item.color, item.name, `${number(item.count)} events`,
      item.mark, `data-analytics-branch="${escapeHtml(item.id)}"`, active, branch && branch !== item.id && !item.count));
    for (const person of residents) {
      const count = item.kinds.reduce((total, itemKind) => total + (person.by_kind[itemKind] || 0), 0);
      if (!count) continue;
      const selected = (!agent || agent === person.id) && (!branch || branch === item.id);
      links.push(`<path class="analytics-link${selected ? " is-active" : ""}" d="M${person.x},${person.y} C${(person.x + item.x) / 2},${person.y} ${(person.x + item.x) / 2},${item.y} ${item.x},${item.y}" style="--node-color:${item.color}" stroke-width="${Math.min(7, 1.2 + count / 4)}"/>`);
    }
  });
  const focused = $("#analytics-network").contains(document.activeElement) ? document.activeElement : null;
  const focusSelector = focused?.dataset.analyticsAgent ? `[data-analytics-agent="${focused.dataset.analyticsAgent}"]`
    : focused?.dataset.analyticsBranch ? `[data-analytics-branch="${focused.dataset.analyticsBranch}"]`
      : focused?.hasAttribute("data-analytics-reset") ? "[data-analytics-reset]" : null;
  $("#analytics-network-content").innerHTML = `<circle cx="${cx}" cy="${cy}" r="${mobile ? 150 : 230}" fill="url(#analytics-glow)"/>
    ${links.join("")}
    <g class="analytics-hub" role="button" tabindex="0" aria-label="Show all session events" data-analytics-reset transform="translate(${cx} ${cy})${mobile ? " scale(.7)" : ""}">
      <circle r="34" /><text dy="4">All</text>
    </g>
    ${dots.join("")}`;
  if (focusSelector) $("#analytics-network-content").querySelector(focusSelector)?.focus({ preventScroll: true });
  transform();
}

function node(x, y, radius, hue, title, subtitle, mark, attrs, active, dim) {
  return `<g class="analytics-node${active ? " is-selected" : ""}${dim ? " is-dim" : ""}" transform="translate(${x} ${y})" style="--node-color:${hue}" role="button" tabindex="0" aria-pressed="${active}" aria-label="${escapeHtml(`${title}: ${subtitle}`)}" ${attrs}>
    <circle r="${radius}"></circle><text class="analytics-node-mark" dy="4">${escapeHtml(mark)}</text>
    <text class="analytics-node-title" y="${radius + 14}">${escapeHtml(title)}</text></g>`;
}

function renderEvents() {
  const container = $("#analytics-events");
  const focused = document.activeElement?.closest("[data-analytics-event]")?.dataset.analyticsEvent;
  container.innerHTML = events.length ? events.map((event) => {
    const branchItem = summary.branches.find((item) => item.kinds.includes(event.kind)) || { color: "#8d8a82" };
    const text = event.text.length > 220 ? `${event.text.slice(0, 219)}…` : event.text;
    return `<button class="analytics-event${selectedEvent === event.id ? " is-selected" : ""}" data-analytics-event="${event.id}" aria-pressed="${selectedEvent === event.id}" style="--node-color:${branchItem.color}">
      <span class="analytics-event-meta"><span>${escapeHtml(label(event.kind))}</span><time>Day ${event.day} · ${escapeHtml(event.time)}</time></span>
      <span class="analytics-event-text">${escapeHtml(text)}</span><span class="analytics-event-people">${escapeHtml(event.agents.map((id) => summary.residents.find((person) => person.id === id)?.name || id).join(" · ") || "Village-wide")}</span></button>`;
  }).join("") : '<p class="analytics-empty">No moments match these filters.<br>Try another villager or event type.</p>';
  if (focused) container.querySelector(`[data-analytics-event="${focused}"]`)?.focus({ preventScroll: true });
  $("#analytics-more").hidden = nextBefore == null;
}

function renderSelection(data) {
  const event = events.find((item) => item.id === selectedEvent);
  const resident = data.residents.find((person) => person.id === agent);
  $("#analytics-selection").innerHTML = event
    ? `<span class="analytics-selection-kicker">A MOMENT IN THE MAP · DAY ${event.day} / ${escapeHtml(event.time)}</span><p>${escapeHtml(event.text)}</p><small>Highlighted nodes show the villagers and event branch involved.</small>`
    : resident
      ? `<span class="analytics-selection-kicker">${escapeHtml(resident.name.toUpperCase())} / ${escapeHtml(resident.role.toUpperCase())}</span><p>${escapeHtml(resident.activity)}${resident.mood ? ` · ${escapeHtml(resident.mood)}` : ""}</p><small>${number(resident.work_count)} tasks completed · ${number(resident.event_count)} events · Health ${Math.round(resident.health)}% · Happiness ${Math.round(resident.happiness ?? 60)}%</small>`
      : '<span class="analytics-selection-kicker">FOLLOW A THREAD</span><p>Small lives. An interconnected story.</p><small>Select a villager, an event branch, or a moment in the timeline to explore its connections.</small>';
}

function filterChanged() {
  selectedEvent = null;
  $("#analytics-events").scrollTop = 0;
  refresh(false);
}

function transform() {
  const box = $("#analytics-network").viewBox.baseVal;
  const x = box.width / 2;
  const y = box.height / 2;
  $("#analytics-network-content").setAttribute("transform", `translate(${pan.x} ${pan.y}) translate(${x} ${y}) scale(${zoom}) translate(${-x} ${-y})`);
  $("#analytics-zoom-out").disabled = zoom <= 0.7;
  $("#analytics-zoom-in").disabled = zoom >= 2;
}

function activate(event) {
  const target = event.target.closest("[data-analytics-agent], [data-analytics-branch], [data-analytics-reset]");
  if (!target) return;
  event.preventDefault();
  if (target.hasAttribute("data-analytics-reset")) resetFilters();
  else if (target.hasAttribute("data-analytics-agent")) agent = agent === target.dataset.analyticsAgent ? "" : target.dataset.analyticsAgent;
  else if (target.hasAttribute("data-analytics-branch")) {
    branch = branch === target.dataset.analyticsBranch ? "" : target.dataset.analyticsBranch;
    kind = "";
  }
  filterChanged();
}

$("#analytics-agent").addEventListener("change", (event) => { agent = event.target.value; filterChanged(); });
$("#analytics-kind").addEventListener("change", (event) => { kind = event.target.value; branch = ""; filterChanged(); });
$("#analytics-search").addEventListener("input", (event) => {
  search = event.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => filterChanged(), 180);
});
$("#analytics-clear").addEventListener("click", () => { resetFilters(); filterChanged(); });
$("#analytics-more").addEventListener("click", () => refresh(true));
$("#analytics-events").addEventListener("click", (event) => {
  const target = event.target.closest("[data-analytics-event]");
  if (!target) return;
  selectedEvent = selectedEvent === Number(target.dataset.analyticsEvent) ? null : Number(target.dataset.analyticsEvent);
  renderEvents();
  renderNetwork(summary);
  renderSelection(summary);
});
$("#analytics-network").addEventListener("click", activate);
$("#analytics-network").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  activate(event);
});
$("#analytics-zoom-in").addEventListener("click", () => { zoom = Math.min(2, zoom + 0.2); transform(); });
$("#analytics-zoom-out").addEventListener("click", () => { zoom = Math.max(0.7, zoom - 0.2); transform(); });
$("#analytics-fit").addEventListener("click", () => { zoom = 1; pan = { x: 0, y: 0 }; transform(); });
const svg = $("#analytics-network");
svg.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".analytics-node, .analytics-hub")) return;
  svg.setPointerCapture(event.pointerId);
  const origin = { x: event.clientX, y: event.clientY, pan: { ...pan } };
  const move = (next) => { pan = { x: origin.pan.x + next.clientX - origin.x, y: origin.pan.y + next.clientY - origin.y }; transform(); };
  const end = () => { svg.removeEventListener("pointermove", move); svg.removeEventListener("pointerup", end); };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerup", end);
});

window.VillageAnalytics = {
  setVisible(open) {
    visible = open;
    if (open) refresh(false);
  },
  update(next, options) {
    const incoming = options.recording ? `replay:${options.replayIndex}` : next.session_id;
    const changedStory = incoming !== (context.recording ? `replay:${context.replayIndex}` : snapshot?.session_id);
    snapshot = next;
    context = options;
    if (!visible) return;
    if (changedStory) {
      events = [];
      selectedEvent = null;
    }
    refresh(false);
  },
};
})();