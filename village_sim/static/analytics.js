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
let requestVersion = 0;
let loading = false;
let pendingUpdate = false;
let failedRequest = null;
let loadedRevision = null;
const safeColor = (value) => /^#[0-9a-f]{6}$/i.test(value) ? value : "#8d8a82";

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
  clearTimeout(searchTimer);
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
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)}d ${Math.floor(minutes % 1440 / 60)}h`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours <= 0) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function kindsForRequest() {
  if (kind) return [kind];
  if (branch) return (summary?.branches || BRANCHES).find((item) => item.id === branch)?.kinds || [];
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
    event_count: source.event_count ?? (source.events || []).length,
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
  if (more && (loading || nextBefore == null)) return;
  clearTimeout(searchTimer);
  requestController?.abort();
  const version = ++requestVersion;
  const source = snapshot;
  const controller = new AbortController();
  requestController = controller;
  loading = true;
  failedRequest = null;
  $("#analytics-timeline").setAttribute("aria-busy", "true");
  $("#analytics-more").disabled = true;
  $("#analytics-refresh").disabled = true;
  setStatus(more ? "Loading earlier events…" : "Loading village activity…");
  const query = {
    agent,
    kinds: kindsForRequest(),
    search,
    before: more ? nextBefore : null,
    limit: 40,
  };
  const params = new URLSearchParams();
  if (query.agent) params.set("agent", query.agent);
  for (const item of query.kinds) params.append("kind", item);
  if (query.search) params.set("search", query.search);
  if (query.before != null) params.set("before", String(query.before));
  params.set("limit", String(query.limit));
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    let data;
    if (context.recording) {
      data = localReport(source, query);
    } else {
      const response = await fetch(`/api/analytics?${params}`, { signal: controller.signal });
      if (!response.ok) throw new Error("Village activity could not be loaded.");
      data = await response.json();
      if (source.session_id && data.session_id !== source.session_id) throw new Error("The village session changed. Refresh to load it.");
    }
    if (version !== requestVersion || !visible) return;
    applyReport(data, more);
    // Paging through older events must not advance the revision of the first page.
    if (!more) loadedRevision = data.revision ?? source.revision;
    pendingUpdate = !context.recording && snapshot.revision > loadedRevision;
  } catch (error) {
    if (version !== requestVersion || !visible) return;
    failedRequest = more;
    setStatus(error.name === "AbortError" ? "Loading took too long. Please retry." : "Couldn’t load activity. Please retry.", true);
    if (!events.length) $("#analytics-events").innerHTML = '<p class="analytics-empty"><strong>Activity is unavailable</strong>Use Retry above to load the event log.</p>';
  } finally {
    clearTimeout(timeout);
    if (version === requestVersion) {
      loading = false;
      $("#analytics-timeline").setAttribute("aria-busy", "false");
      $("#analytics-more").disabled = false;
      $("#analytics-refresh").disabled = false;
      renderUpdateStatus();
    }
  }
}

function renderUpdateStatus() {
  if (loading) return;
  $("#analytics-refresh span").textContent = failedRequest !== null ? "Retry" : pendingUpdate ? "Show latest" : "Refresh";
  $("#analytics-refresh").classList.toggle("has-updates", pendingUpdate);
  if (failedRequest !== null) return;
  setStatus(summary?.history_complete === false
    ? "This recording contains only a recent window of events."
    : pendingUpdate ? "New activity available. Your place in the log is saved."
      : context.recording ? "Activity at this point in the recorded story."
        : `Updated through day ${snapshot.day} · ${snapshot.time}`);
}

function applyReport(data, more) {
  summary = data;
  nextBefore = data.next_before;
  events = more ? [...new Map([...events, ...data.events].map((event) => [event.id, event])).values()] : data.events;
  if (!events.some((event) => event.id === selectedEvent)) selectedEvent = null;
  renderStats(data);
  renderFilters(data);
  renderBreakdown(data);
  renderNetwork(data);
  renderEvents();
  renderSelection(data);
}

function renderStats(data) {
  $("#analytics-stats").innerHTML = [
    ["Events", number(data.event_count), "in this session"],
    ["Villagers", number(data.residents.filter((person) => person.active).length), `${number(data.residents.length)} in this story`],
    ["Tasks", number(data.tasks_completed), "completed"],
    ["Village time", duration(data.elapsed_minutes), "of village life"],
  ].map(([title, value, caption]) => `<div class="analytics-stat"><span>${title}</span><strong>${value}</strong><small>${caption}</small></div>`).join("");
  renderMode();
}

function renderMode() {
  $("#analytics-mode").textContent = context.recording ? "Recorded story" : context.connected === false ? "Reconnecting" : snapshot.paused ? "Session paused" : "Live session";
  $("#analytics-mode").title = $("#analytics-mode").textContent;
  $("#analytics-mode").classList.toggle("is-paused", Boolean(context.recording || snapshot.paused));
}

function renderFilters(data) {
  const agentOptions = `<option value="">All villagers</option>` + data.residents.map((person) =>
    `<option value="${escapeHtml(person.id)}">${escapeHtml(person.name)}${person.active ? "" : " · away"}</option>`).join("");
  if ($("#analytics-agent").innerHTML !== agentOptions) $("#analytics-agent").innerHTML = agentOptions;
  $("#analytics-agent").value = agent;
  const category = data.branches.find((item) => item.id === branch);
  const kindOptions = `<option value="">${category ? `All ${escapeHtml(category.name.toLowerCase())} types` : "All event types"}</option>` + Object.keys(data.by_kind).filter((item) => !category || category.kinds.includes(item)).sort().map((item) =>
    `<option value="${escapeHtml(item)}">${escapeHtml(label(item))}</option>`).join("");
  if ($("#analytics-kind").innerHTML !== kindOptions) $("#analytics-kind").innerHTML = kindOptions;
  $("#analytics-kind").value = kind;
  $("#analytics-clear").hidden = !filtered();
  $("#analytics-filter-note span").textContent = [
    data.residents.find((person) => person.id === agent)?.name,
    kind ? label(kind) : category?.name,
    search ? `“${search}”` : "",
  ].filter(Boolean).join(" · ") || "All session events";
  $("#analytics-event-count").textContent = number(data.matching_events);
}

function renderBreakdown(data) {
  const resident = data.residents.find((person) => person.id === agent);
  const counts = resident?.by_kind || data.by_kind;
  const totals = data.branches.map((item) => item.kinds.reduce((sum, type) => sum + (counts[type] || 0), 0));
  const largest = Math.max(1, ...totals);
  $("#analytics-breakdown-note").textContent = resident ? `${resident.name}’s events · select a category` : "Events by category · select to explore";
  const focused = document.activeElement?.closest("#analytics-breakdown [data-analytics-branch]")?.dataset.analyticsBranch;
  $("#analytics-breakdown").innerHTML = data.branches.map((item, index) => `<button class="analytics-category" data-analytics-branch="${escapeHtml(item.id)}" aria-pressed="${branch === item.id || Boolean(kind && item.kinds.includes(kind))}" style="--node-color:${safeColor(item.color)}">
    <span class="analytics-category-mark" aria-hidden="true">${escapeHtml(item.mark)}</span>
    <span class="analytics-category-label">${escapeHtml(item.name)}<span class="analytics-category-track" aria-hidden="true"><i style="width:${totals[index] / largest * 100}%"></i></span></span>
    <span class="analytics-category-count">${number(totals[index])}<span class="sr-only"> events</span></span></button>`).join("");
  if (focused) [...$("#analytics-breakdown").children].find((button) => button.dataset.analyticsBranch === focused)?.focus({ preventScroll: true });
}

function renderNetwork(data) {
  const residents = data.residents.filter((person) => person.event_count || person.active);
  const width = 520;
  const height = Math.max(390, residents.length * 62 + 30);
  const cx = width / 2;
  const cy = height / 2;
  $("#analytics-network").setAttribute("viewBox", `0 0 ${width} ${height}`);
  $("#analytics-network").style.height = "auto";
  const selected = events.find((event) => event.id === selectedEvent);
  const focusedPeople = selected ? selected.agents : agent ? [agent] : [];
  const focusedKinds = selected ? [selected.kind] : kindsForRequest();
  const dots = [];
  residents.forEach((person, index) => {
    const x = 88;
    const y = 30 + (index + .5) / residents.length * (height - 80);
    const active = focusedPeople.includes(person.id);
    dots.push(node(x, y, 19, safeColor(person.color), person.name,
      `${number(person.event_count)} events · ${person.active ? person.activity : "away"}`,
      person.name.charAt(0), `data-analytics-agent="${escapeHtml(person.id)}"`, active, (selected || agent) && !active));
    person.x = x;
    person.y = y;
  });
  const links = [];
  data.branches.forEach((item, index) => {
    item.x = width - 88;
    item.y = 30 + (index + .5) / data.branches.length * (height - 80);
    const active = focusedKinds.some((type) => item.kinds.includes(type));
    dots.push(node(item.x, item.y, 19, safeColor(item.color), item.name, `${number(item.count)} events`,
      item.mark, `data-analytics-branch="${escapeHtml(item.id)}"`, active, focusedKinds.length && !active));
    for (const person of residents) {
      const count = item.kinds.reduce((total, itemKind) => total + (person.by_kind[itemKind] || 0), 0);
      if (!count) continue;
      const activeLink = (!selected && !agent || focusedPeople.includes(person.id)) && (!focusedKinds.length || active);
      links.push(`<path class="analytics-link${activeLink ? " is-active" : ""}" d="M${person.x},${person.y} C${(person.x + item.x) / 2},${person.y} ${(person.x + item.x) / 2},${item.y} ${item.x},${item.y}" style="--node-color:${safeColor(item.color)}" stroke-width="${Math.min(5, 1.2 + count / 8)}"/>`);
    }
  });
  const focused = $("#analytics-network").contains(document.activeElement) ? document.activeElement : null;
  const focusSelector = focused?.dataset.analyticsAgent ? `[data-analytics-agent="${focused.dataset.analyticsAgent}"]`
    : focused?.dataset.analyticsBranch ? `[data-analytics-branch="${focused.dataset.analyticsBranch}"]`
      : focused?.hasAttribute("data-analytics-reset") ? "[data-analytics-reset]" : null;
  $("#analytics-network-content").innerHTML = `<circle cx="${cx}" cy="${cy}" r="180" fill="url(#analytics-glow)"/>
    ${links.join("")}
    <g class="analytics-hub" role="button" tabindex="0" aria-label="Show all session events" data-analytics-reset transform="translate(${cx} ${cy})">
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
    return `<button class="analytics-event${selectedEvent === event.id ? " is-selected" : ""}" data-analytics-event="${event.id}" aria-expanded="${selectedEvent === event.id}" style="--node-color:${safeColor(branchItem.color)}">
      <span class="analytics-event-meta"><span>${escapeHtml(label(event.kind))}</span><time>Day ${event.day} · ${escapeHtml(event.time)}</time></span>
      <span class="analytics-event-text">${escapeHtml(event.text)}</span><span class="analytics-event-people">${escapeHtml(event.agents.map((id) => summary.residents.find((person) => person.id === id)?.name || id).join(" · ") || "Village-wide")}</span>${selectedEvent === event.id ? '<span class="analytics-event-hint">Selected in village connections · click to collapse</span>' : ""}</button>`;
  }).join("") : filtered()
    ? '<p class="analytics-empty"><strong>No matching moments</strong>Try a different search or clear the filters to see the whole story.</p>'
    : '<p class="analytics-empty"><strong>A story waiting to happen</strong>Village activity will appear here as the simulation advances.</p>';
  if (focused) container.querySelector(`[data-analytics-event="${focused}"]`)?.focus({ preventScroll: true });
  $("#analytics-more").hidden = nextBefore == null;
  $("#analytics-shown").textContent = `${number(events.length)} of ${number(summary.matching_events)} events`;
}

function renderSelection(data) {
  const event = events.find((item) => item.id === selectedEvent);
  const resident = data.residents.find((person) => person.id === agent);
  $("#analytics-selection").innerHTML = event
    ? `<span class="analytics-selection-kicker">SELECTED MOMENT · DAY ${event.day} / ${escapeHtml(event.time)}</span><p>${escapeHtml(event.text)}</p><small>Open Village connections to see who was involved.</small>`
    : resident
      ? `<span class="analytics-selection-kicker">${escapeHtml(resident.name.toUpperCase())} / ${escapeHtml((resident.role || "Villager").toUpperCase())}</span><p>${escapeHtml(resident.activity)}${resident.mood ? ` · ${escapeHtml(resident.mood)}` : ""}</p><small>${number(resident.work_count)} tasks completed · ${number(resident.event_count)} events${resident.health == null ? "" : ` · Health ${Math.round(resident.health)}%`}${resident.happiness == null ? "" : ` · Happiness ${Math.round(resident.happiness)}%`}</small>`
      : '<span class="analytics-selection-kicker">FOLLOW THE STORY</span><p>A little work. A conversation. A change in the weather.</p><small>Select a category to filter the log, or open the map to explore each villager’s connections.</small>';
}

function filterChanged() {
  clearTimeout(searchTimer);
  selectedEvent = null;
  events = [];
  nextBefore = null;
  $("#analytics-events").scrollTop = 0;
  $("#analytics-events").innerHTML = '<p class="analytics-empty">Finding moments…</p>';
  $("#analytics-more").hidden = true;
  $("#analytics-shown").textContent = "";
  if (summary) { renderFilters(summary); renderBreakdown(summary); }
  $("#analytics-event-count").textContent = "…";
  refresh(false);
}

function transform() {
  const box = $("#analytics-network").viewBox.baseVal;
  const x = box.width / 2;
  const y = box.height / 2;
  $("#analytics-network-content").setAttribute("transform", `translate(${pan.x} ${pan.y}) translate(${x} ${y}) scale(${zoom}) translate(${-x} ${-y})`);
  $("#analytics-zoom-out").disabled = zoom <= 0.701;
  $("#analytics-zoom-in").disabled = zoom >= 1.999;
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
  if (window.matchMedia("(max-width: 760px)").matches) setView("events", true);
}

function setView(view, moveFocus = false) {
  $("#analytics-panel").dataset.view = view;
  document.querySelectorAll("[data-analytics-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.analyticsView === view)));
  if (moveFocus) $(`[data-analytics-view="${view}"]`).focus({ preventScroll: true });
}
document.querySelectorAll("[data-analytics-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.analyticsView)));
$("#analytics-breakdown").addEventListener("click", activate);
$("#analytics-agent").addEventListener("change", (event) => { agent = event.target.value; filterChanged(); });
$("#analytics-kind").addEventListener("change", (event) => { kind = event.target.value; filterChanged(); });
$("#analytics-search").addEventListener("input", (event) => {
  search = event.target.value;
  // Invalidate the old query immediately, including during the debounce window.
  requestController?.abort();
  requestVersion++;
  loading = true;
  $("#analytics-more").disabled = true;
  $("#analytics-refresh").disabled = true;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => filterChanged(), 180);
});
$("#analytics-clear").addEventListener("click", () => { resetFilters(); filterChanged(); });
$("#analytics-more").addEventListener("click", () => refresh(true));
$("#analytics-refresh").addEventListener("click", () => {
  const more = failedRequest === true;
  if (!more) $("#analytics-events").scrollTop = 0;
  refresh(more);
});
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
  // Let touch users scroll the overview; zoom buttons work for every input type.
  if (event.pointerType === "touch" || event.button !== 0 || event.target.closest(".analytics-node, .analytics-hub")) return;
  svg.setPointerCapture(event.pointerId);
  const matrix = svg.getScreenCTM()?.inverse();
  if (!matrix) return;
  const origin = { x: event.clientX, y: event.clientY, pan: { ...pan } };
  const move = (next) => {
    pan = { x: origin.pan.x + (next.clientX - origin.x) * matrix.a, y: origin.pan.y + (next.clientY - origin.y) * matrix.d };
    transform();
  };
  const end = () => {
    svg.removeEventListener("pointermove", move);
    svg.removeEventListener("pointerup", end);
    svg.removeEventListener("pointercancel", end);
    svg.removeEventListener("lostpointercapture", end);
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("lostpointercapture", end);
});

window.VillageAnalytics = {
  setVisible(open) {
    visible = open;
    if (open) refresh(false);
    else {
      clearTimeout(searchTimer);
      requestController?.abort();
      requestVersion++;
      loading = false;
      $("#analytics-timeline").setAttribute("aria-busy", "false");
    }
  },
  update(next, options) {
    const changedStory = options.recording !== context.recording || (!options.recording && next.session_id !== snapshot?.session_id);
    const changedFrame = options.recording && options.replayIndex !== context.replayIndex;
    snapshot = next;
    context = options;
    if (changedStory || changedFrame) {
      requestController?.abort();
      requestVersion++;
      loading = false;
      events = [];
      nextBefore = null;
      selectedEvent = null;
      loadedRevision = null;
      pendingUpdate = false;
      failedRequest = null;
      if (changedStory) {
        resetFilters();
        summary = null;
        zoom = 1;
        pan = { x: 0, y: 0 };
        for (const id of ["stats", "breakdown", "network-content", "selection", "shown"]) $("#analytics-" + id).replaceChildren();
        $("#analytics-agent").innerHTML = '<option value="">All villagers</option>';
        $("#analytics-kind").innerHTML = '<option value="">All event types</option>';
        $("#analytics-filter-note span").textContent = "All session events";
        $("#analytics-event-count").textContent = "…";
        $("#analytics-events").innerHTML = '<p class="analytics-empty">Loading village activity…</p>';
        $("#analytics-clear").hidden = true;
        $("#analytics-more").hidden = true;
      }
    }
    if (!visible) return;
    renderMode();
    if (changedStory || changedFrame || (!summary && !loading && failedRequest === null)) {
      filterChanged();
    } else if (!options.recording && next.revision > loadedRevision) {
      pendingUpdate = true;
      renderUpdateStatus();
    }
  },
};
})();
