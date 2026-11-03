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
let mapSummary = null;
let mapController = null;
let mapTimer = null;
let previewNode = null;
let mapFailed = false;
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
  mapFailed = false;
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
  if (mapFailed) {
    setStatus("The live map couldn’t update. Use Show latest to retry.", true);
    return;
  }
  setStatus(summary?.history_complete === false
    ? "This recording contains only a recent window of events."
    : pendingUpdate ? "New events available. Your place in the log is saved."
      : context.recording ? "Activity at this point in the recorded story."
        : `Updated through day ${snapshot.day} · ${snapshot.time}`);
}

function applyReport(data, more) {
  summary = data;
  nextBefore = data.next_before;
  events = more ? [...new Map([...events, ...data.events].map((event) => [event.id, event])).values()] : data.events;
  if (!events.some((event) => event.id === selectedEvent)) selectedEvent = null;
  renderFilters(data);
  renderMapReport(data);
  renderEvents();
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
  $("#analytics-breakdown-note").textContent = resident ? `${resident.name}’s events` : "All villagers";
  const focused = document.activeElement?.closest("#analytics-breakdown [data-analytics-branch]")?.dataset.analyticsBranch;
  $("#analytics-breakdown").innerHTML = data.branches.map((item, index) => `<button class="analytics-category" data-analytics-branch="${escapeHtml(item.id)}" aria-pressed="${branch === item.id || Boolean(kind && item.kinds.includes(kind))}" style="--node-color:${safeColor(item.color)}">
    <span class="analytics-category-mark" aria-hidden="true">${escapeHtml(item.mark)}</span>
    <span class="analytics-category-label">${escapeHtml(item.name)}<span class="analytics-category-track" aria-hidden="true"><i style="width:${totals[index] / largest * 100}%"></i></span></span>
    <span class="analytics-category-count">${number(totals[index])}<span class="sr-only"> events</span></span></button>`).join("");
  if (focused) [...$("#analytics-breakdown").children].find((button) => button.dataset.analyticsBranch === focused)?.focus({ preventScroll: true });
}

function renderMapReport(data) {
  if (!mapSummary || !(data.revision < mapSummary.revision)) mapSummary = data;
  renderStats(mapSummary);
  renderBreakdown(mapSummary);
  renderNetwork(mapSummary);
  renderSelection(mapSummary);
}

function scheduleMapUpdate() {
  if (mapTimer || mapController || mapFailed || loading || failedRequest !== null || context.recording || mapSummary?.revision >= snapshot.revision) return;
  mapTimer = setTimeout(async () => {
    mapTimer = null;
    if (!visible || loading || context.recording) return;
    const story = snapshot.session_id;
    const controller = new AbortController();
    mapController = controller;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const response = await fetch("/api/analytics?limit=1", { signal: controller.signal });
      if (!response.ok) throw new Error("Map update failed");
      const data = await response.json();
      if (!visible || controller.signal.aborted || context.recording || snapshot.session_id !== story || data.session_id !== story) return;
      // Updating the graph must never replace the timeline, its cursor or selection.
      renderMapReport(data);
    } catch (error) {
      if ((!controller.signal.aborted || timedOut) && visible && !context.recording && snapshot.session_id === story) {
        mapFailed = true;
        renderUpdateStatus();
      }
    } finally {
      clearTimeout(timeout);
      if (mapController === controller) mapController = null;
    }
  }, 2000);
}

function renderNetwork(data) {
  const mobile = window.matchMedia("(max-width: 760px)").matches;
  const residents = data.residents.filter((person) => person.event_count || person.active);
  const width = mobile ? 640 : 1000;
  const height = mobile ? Math.max(660, residents.length * 82 + 100) : 660;
  const cx = width / 2;
  const cy = height / 2;
  $("#analytics-network").setAttribute("viewBox", `0 0 ${width} ${height}`);
  const position = (index, total, side) => {
    const t = total === 1 ? .5 : index / (total - 1);
    if (mobile) return {
      x: side === "left" ? 170 - Math.sin(t * Math.PI) * 40 : width - 170 + Math.sin(t * Math.PI) * 65,
      y: 60 + t * (height - 150),
    };
    const angle = (side === "left" ? -126 - t * 108 : -54 + t * 108) * Math.PI / 180;
    return { x: cx + Math.cos(angle) * 325, y: cy + Math.sin(angle) * 255 };
  };
  const people = residents.map((person, i) => ({ ...person, ...position(i, residents.length, "left") }));
  const branches = data.branches.map((item, i) => ({ ...item, ...position(i, data.branches.length, "right") }));
  const links = [];
  const spokes = [];
  const nodes = [];
  const maxCount = Math.max(1, ...people.flatMap((person) => branches.map((item) => item.kinds.reduce((sum, type) => sum + (person.by_kind[type] || 0), 0))));
  for (const person of people) {
    nodes.push(node(person, "agent", person.name, `${number(person.event_count)} events${person.active ? "" : " · away"}`, person.name.charAt(0), mobile));
    spokes.push(`<path class="analytics-spoke" data-map-agent="${escapeHtml(person.id)}" d="M${cx - 65},${cy} Q${cx - 135},${person.y} ${person.x},${person.y}"/>`);
    for (const item of branches) {
      const count = item.kinds.reduce((sum, type) => sum + (person.by_kind[type] || 0), 0);
      if (!count) continue;
      // Bow the threads around the hub so the centre and labels remain readable.
      const bend = cy + (person.y + item.y < cy * 2 ? -130 : 130);
      const path = `M${person.x},${person.y} C${cx - 110},${bend} ${cx + 110},${bend} ${item.x},${item.y}`;
      const attrs = `data-map-agent="${escapeHtml(person.id)}" data-map-branch="${escapeHtml(item.id)}"`;
      const title = escapeHtml(`${person.name} ↔ ${item.name}: ${number(count)} shared events`);
      links.push(`<path class="analytics-link" ${attrs} d="${path}" style="--node-color:${safeColor(item.color)}" stroke-width="${1 + 4 * Math.sqrt(count / maxCount)}"><title>${title}</title></path>
        <path class="analytics-signal" ${attrs} d="${path}" pathLength="100" style="--node-color:${safeColor(item.color)}"/>`);
    }
  }
  for (const item of branches) {
    nodes.push(node(item, "branch", item.name, `${number(item.count)} events`, item.mark, mobile));
    spokes.push(`<path class="analytics-spoke" data-map-branch="${escapeHtml(item.id)}" d="M${cx + 65},${cy} Q${cx + 135},${item.y} ${item.x},${item.y}"/>`);
  }
  const focused = $("#analytics-network").contains(document.activeElement) ? document.activeElement : null;
  const focusAgent = focused?.dataset.analyticsAgent;
  const focusBranch = focused?.dataset.analyticsBranch;
  const focusHub = focused?.hasAttribute("data-analytics-reset");
  $("#analytics-network-content").innerHTML = `
    <circle class="analytics-ambient" cx="${cx}" cy="${cy}" r="250" fill="url(#analytics-glow)"/>
    <ellipse class="analytics-orbit" cx="${cx}" cy="${cy}" rx="${mobile ? 225 : 325}" ry="${mobile ? height / 2 - 65 : 255}"/>
    ${spokes.join("")}${links.join("")}
    <g class="analytics-hub" role="button" tabindex="0" aria-label="Lantern Bay simulation. Show all session events" data-analytics-reset transform="translate(${cx} ${cy})">
      <circle class="analytics-hub-aura" r="90"/>
      <circle class="analytics-hub-ring" r="76"/>
      <circle class="analytics-hub-core" r="64"/>
      <text class="analytics-hub-name" y="-10">Lantern</text><text class="analytics-hub-name" y="16">Bay</text>
      <text class="analytics-hub-count" y="39">${number(data.event_count)} moments</text>
    </g>${nodes.join("")}`;
  if (focused) [...$("#analytics-network-content").querySelectorAll("[role=button]")].find((item) =>
    focusAgent ? item.dataset.analyticsAgent === focusAgent : focusBranch ? item.dataset.analyticsBranch === focusBranch : focusHub && item.hasAttribute("data-analytics-reset"))?.focus({ preventScroll: true });
  highlightNetwork();
  transform();
}

function node(item, type, title, subtitle, mark, mobile) {
  const below = mobile && type === "branch";
  const anchor = below ? "middle" : type === "agent" ? "end" : "start";
  const x = below ? 0 : type === "agent" ? -37 : 37;
  const y = below ? 46 : -3;
  return `<g class="analytics-node analytics-node-${type}" transform="translate(${item.x} ${item.y})" style="--node-color:${safeColor(item.color)}" role="button" tabindex="0" aria-pressed="false" aria-label="${escapeHtml(`${title}: ${subtitle}`)}" data-analytics-${type}="${escapeHtml(item.id)}">
    <title>${escapeHtml(`${title}: ${subtitle}. Select to filter the event log.`)}</title>
    <circle class="analytics-node-target" r="34"/><circle class="analytics-node-halo" r="29"/>
    <circle class="analytics-node-core" r="21"/><text class="analytics-node-mark" dy="5">${escapeHtml(mark)}</text>
    <text class="analytics-node-title" x="${x}" y="${y}" text-anchor="${anchor}">${escapeHtml(title)}</text>
    <text class="analytics-node-count" x="${x}" y="${y + 20}" text-anchor="${anchor}">${escapeHtml(subtitle)}</text></g>`;
}

function highlightNetwork() {
  if (!mapSummary) return;
  const selected = events.find((event) => event.id === selectedEvent);
  let people = selected ? selected.agents : agent ? [agent] : [];
  let types = selected ? [selected.kind] : kindsForRequest();
  if (previewNode) {
    people = previewNode.agent ? [previewNode.agent] : [];
    types = previewNode.branch ? mapSummary.branches.find((item) => item.id === previewNode.branch)?.kinds || [] : [];
  }
  const branchIds = mapSummary.branches.filter((item) => item.kinds.some((type) => types.includes(type))).map((item) => item.id);
  const person = people.length === 1 ? mapSummary.residents.find((item) => people.includes(item.id)) : null;
  const category = branchIds.length === 1 ? mapSummary.branches.find((item) => branchIds.includes(item.id)) : null;
  const mobile = window.matchMedia("(max-width: 760px)").matches;
  const focused = Boolean(selected || people.length || types.length);
  const connectedPeople = new Set();
  const connectedBranches = new Set();
  for (const path of $("#analytics-network").querySelectorAll(".analytics-link, .analytics-signal")) {
    const active = (!people.length || people.includes(path.dataset.mapAgent)) && (!types.length || branchIds.includes(path.dataset.mapBranch));
    path.classList.toggle("is-active", active);
    path.classList.toggle("is-muted", focused && !active);
    if (active && focused) { connectedPeople.add(path.dataset.mapAgent); connectedBranches.add(path.dataset.mapBranch); }
  }
  for (const item of $("#analytics-network").querySelectorAll(".analytics-node")) {
    const id = item.dataset.analyticsAgent || item.dataset.analyticsBranch;
    const isPerson = item.hasAttribute("data-analytics-agent");
    const active = isPerson ? people.includes(id) : branchIds.includes(id);
    const connected = isPerson ? connectedPeople.has(id) : connectedBranches.has(id);
    item.classList.toggle("is-selected", active);
    item.classList.toggle("is-connected", focused && connected);
    item.classList.toggle("is-dim", focused && !active && !connected);
    const source = isPerson ? mapSummary.residents.find((entry) => entry.id === id) : mapSummary.branches.find((entry) => entry.id === id);
    const shared = isPerson && category
      ? category.kinds.reduce((sum, type) => sum + (source?.by_kind[type] || 0), 0)
      : !isPerson && person ? (source?.kinds || []).reduce((sum, type) => sum + (person.by_kind[type] || 0), 0) : null;
    const total = number(isPerson ? source?.event_count : source?.count);
    const caption = isPerson && !source?.active ? `${total}${mobile ? "" : " events"} · away` : `${total} events`;
    item.querySelector(".analytics-node-count").textContent = shared == null ? caption : `${number(shared)} shared`;
    // The pressed state describes the persistent filter, independently of hover.
    item.setAttribute("aria-pressed", String(isPerson ? agent === id : branch === id || Boolean(kind && mapSummary.branches.find((entry) => entry.id === id)?.kinds.includes(kind))));
  }
  for (const path of $("#analytics-network").querySelectorAll(".analytics-spoke")) {
    path.classList.toggle("is-active", Boolean(path.dataset.mapAgent ? people.includes(path.dataset.mapAgent) : branchIds.includes(path.dataset.mapBranch)));
  }
  const count = person && category ? category.kinds.reduce((sum, type) => sum + (person.by_kind[type] || 0), 0) : person?.event_count ?? category?.count;
  $("#analytics-network-note").textContent = person || category
    ? `${[person?.name, category?.name].filter(Boolean).join(" ↔ ")} · ${number(count)} ${person && category ? "shared " : ""}events`
    : "Thicker threads mean more shared events. Hover to trace a connection.";
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
  const category = data.branches.find((item) => item.id === branch || kind && item.kinds.includes(kind));
  const contributors = category ? data.residents.map((person) => ({ name: person.name,
    count: category.kinds.reduce((sum, type) => sum + (person.by_kind[type] || 0), 0),
  })).filter((person) => person.count).sort((a, b) => b.count - a.count).slice(0, 3) : [];
  $("#analytics-selection").innerHTML = event
    ? `<span class="analytics-selection-kicker">SELECTED MOMENT · DAY ${event.day} / ${escapeHtml(event.time)}</span><p>${escapeHtml(event.text)}</p><small>Highlighted threads show the villagers and simulation branch involved.</small>`
    : resident
      ? `<span class="analytics-selection-kicker">${escapeHtml(resident.name.toUpperCase())} / ${escapeHtml((resident.role || "Villager").toUpperCase())}</span><p>${escapeHtml(resident.activity)}${resident.mood ? ` · ${escapeHtml(resident.mood)}` : ""}</p><small>${number(resident.work_count)} tasks completed · ${number(resident.event_count)} events${resident.health == null ? "" : ` · Health ${Math.round(resident.health)}%`}${resident.happiness == null ? "" : ` · Happiness ${Math.round(resident.happiness)}%`}</small>`
      : category
        ? `<span class="analytics-selection-kicker">${escapeHtml(category.name.toUpperCase())} / SIMULATION BRANCH</span><p>${number(category.count)} events${contributors.length ? ` · ${escapeHtml(contributors.map((person) => `${person.name} (${number(person.count)})`).join(" · "))}` : ""}</p><small>Select a villager to explore their participation in this branch.</small>`
      : '<span class="analytics-selection-kicker">FOLLOW THE STORY</span><p>A little work. A conversation. A change in the weather.</p><small>Select a glowing node to explore its connections. The centre returns to the whole village.</small>';
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
  if (summary) { renderFilters(summary); renderBreakdown(mapSummary || summary); highlightNetwork(); }
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
  renderNetwork(mapSummary || summary);
  renderSelection(mapSummary || summary);
});
$("#analytics-network").addEventListener("click", activate);
$("#analytics-network").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  activate(event);
});
function preview(target) {
  const item = target?.closest?.(".analytics-node");
  previewNode = item ? { agent: item.dataset.analyticsAgent, branch: item.dataset.analyticsBranch } : null;
  highlightNetwork();
}
$("#analytics-network").addEventListener("pointerover", (event) => preview(event.target));
$("#analytics-network").addEventListener("pointerleave", () => { previewNode = null; highlightNetwork(); });
$("#analytics-network").addEventListener("focusin", (event) => preview(event.target));
$("#analytics-network").addEventListener("focusout", (event) => preview(event.relatedTarget));
window.matchMedia("(max-width: 760px)").addEventListener("change", () => {
  if (visible && mapSummary) { zoom = 1; pan = { x: 0, y: 0 }; renderNetwork(mapSummary); }
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
    if (open) { setView("activity"); refresh(false); }
    else {
      clearTimeout(searchTimer);
      clearTimeout(mapTimer);
      mapTimer = null;
      mapController?.abort();
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
      clearTimeout(mapTimer);
      mapTimer = null;
      mapController?.abort();
      mapSummary = null;
      mapFailed = false;
      previewNode = null;
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
      scheduleMapUpdate();
    }
  },
};
})();
