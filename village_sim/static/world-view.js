"use strict";

// Draws Lantern Bay on the same 40×23 grid the simulation walks.
// A place foot is ((x + 0.5) * 32, (y + 0.5) * 32) inside the 1280×720 map,
// which is the percentage the DOM markers already use. Art comes from
// /assets/manifest.json. If that file is missing, the painted backdrop stays.

const VillageView = {
  ready: false,
  manifest: null,
  images: new Map(),
  sim: null,
  terrain: [null, null],
  ripples: [],
  silhouettes: new Map(),
  reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
};

window.VillageView = VillageView;

VillageView.portrait = (id) => {
  const file = VillageView.manifest?.characters?.[id]?.actions?.idle?.s
    || VillageView.manifest?.characters?.visitor?.actions?.idle?.s;
  return file ? `/assets/${file}` : "";
};

VillageView.sync = (state, world) => {
  if (!state || !world) return;
  VillageView.sim = {
    places: world.places,
    pantry: state.pantry || {},
    population: state.agents?.length || state.villager_count || 1,
    centers: state.collection_centers || null,
    waterSources: state.water_sources || null,
    step: state.step || 0,
    minute: state.minute ?? 1080,
    weather: state.weather,
    festival: state.festival,
  };
};

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Could not load ${url}`));
    image.src = url;
  });
}

function assetFiles(manifest) {
  const files = new Set(Object.values(manifest.tiles));
  for (const item of [...manifest.buildings, ...manifest.resources, ...manifest.scenery]) {
    files.add(item.file);
    for (const file of Object.values(item.states || {})) files.add(file);
  }
  for (const person of Object.values(manifest.characters)) {
    for (const views of Object.values(person.actions)) {
      for (const file of Object.values(views)) files.add(file);
    }
  }
  return files;
}

// Stable, world-space noise keeps the landscape still as the camera moves.
function noise(x, y, seed = 0) {
  let n = Math.imul(x + seed * 131, 374761393) + Math.imul(y, 668265263);
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967295;
}

function surface(width = 1280, height = 720) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function terrainAt(x, y) {
  const manifest = VillageView.manifest;
  if (x < 0 || y < 0 || x >= manifest.map.width || y >= manifest.map.height) return "water";
  return manifest.terrain[y * manifest.map.width + x];
}

function paintTiles(manifest, mask, names) {
  const canvas = surface(...manifest.map.canvas);
  const ctx = canvas.getContext("2d");
  for (let y = 0; y < manifest.map.height; y++) {
    for (let x = 0; x < manifest.map.width; x++) {
      const name = names[Math.floor(noise(x, y, 7) * names.length) % names.length];
      ctx.drawImage(VillageView.images.get(manifest.tiles[name]), x * 32, y * 32);
    }
  }
  ctx.globalCompositeOperation = "destination-in";
  ctx.drawImage(mask, 0, 0);
  return canvas;
}

function bakeTerrain(manifest) {
  const [width, height] = manifest.map.canvas;
  const canvas = surface(width, height);
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;

  // Interpolate the existing grid, never move its tile centres. Two-pixel
  // contour steps soften the coast and paths while retaining crisp pixel art.
  const masks = [surface(width / 2, height / 2), surface(width / 2, height / 2), surface(width / 2, height / 2)];
  const data = masks.map((mask) => mask.getContext("2d").createImageData(mask.width, mask.height));
  for (let y = 0; y < height / 2; y++) {
    for (let x = 0; x < width / 2; x++) {
      const gx = (x * 2 + 1) / 32 - 0.5, gy = (y * 2 + 1) / 32 - 0.5;
      const tx = Math.floor(gx), ty = Math.floor(gy), fx = gx - tx, fy = gy - ty;
      const coverage = [0, 0, 0];
      for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
        const tile = terrainAt(tx + dx, ty + dy);
        const weight = (dx ? fx : 1 - fx) * (dy ? fy : 1 - fy);
        if (tile !== "water" && tile !== "pier") coverage[0] += weight;
        if (tile.startsWith("grass") || tile.startsWith("path")) coverage[1] += weight;
        if (tile.startsWith("path")) coverage[2] += weight;
      }
      const roughness = (noise(Math.floor(x / 2), Math.floor(y / 2), 12) - 0.5) * 0.12;
      coverage.forEach((value, i) => {
        if (value <= [0.30, 0.48, 0.44][i] + roughness) return;
        const offset = (y * width / 2 + x) * 4;
        data[i].data.set([255, 255, 255, 255], offset);
      });
    }
  }
  const fullMasks = masks.map((mask, i) => {
    mask.getContext("2d").putImageData(data[i], 0, 0);
    const full = surface(width, height);
    const context = full.getContext("2d");
    context.imageSmoothingEnabled = false;
    context.drawImage(mask, 0, 0, width, height);
    return full;
  });

  const sea = ctx.createLinearGradient(0, 0, width * 0.3, height);
  sea.addColorStop(0, "#285e70");
  sea.addColorStop(0.55, "#1b5064");
  sea.addColorStop(1, "#123c51");
  ctx.fillStyle = sea;
  ctx.fillRect(0, 0, width, height);
  ctx.globalAlpha = 0.22;
  for (let y = 0; y < manifest.map.height; y++) for (let x = 0; x < manifest.map.width; x++) {
    const file = manifest.tiles[noise(x, y) > 0.5 ? "water" : "water_b"];
    ctx.drawImage(VillageView.images.get(file), x * 32, y * 32);
  }
  ctx.globalAlpha = 1;

  // A shallow shelf and wet sand give the island a visible waterline.
  ctx.shadowColor = "#62b5aa";
  ctx.shadowBlur = 30;
  ctx.drawImage(fullMasks[0], 0, 0);
  ctx.shadowColor = "#bad1b2";
  ctx.shadowBlur = 5;
  ctx.drawImage(fullMasks[0], 0, 0);
  ctx.shadowBlur = 0;
  ctx.drawImage(paintTiles(manifest, fullMasks[0], ["sand", "sand_b"]), 0, 0);
  ctx.shadowColor = "#5a663e";
  ctx.shadowBlur = 2;
  ctx.shadowOffsetY = 2;
  ctx.drawImage(paintTiles(manifest, fullMasks[1], ["grass", "grass_b", "grass_flower"]), 0, 0);
  ctx.shadowOffsetY = 0;
  ctx.shadowBlur = 0;
  ctx.drawImage(paintTiles(manifest, fullMasks[2], ["path", "path_b"]), 0, 0);

  manifest.terrain.forEach((tile, index) => {
    if (tile !== "pier") return;
    const x = index % manifest.map.width, y = Math.floor(index / manifest.map.width);
    ctx.fillStyle = "#092d3e66";
    ctx.fillRect(x * 32 + 6, y * 32 + 6, 32, 34);
    ctx.drawImage(VillageView.images.get(manifest.tiles.pier), x * 32, y * 32);
    if (terrainAt(x, y + 1) !== "pier") {
      ctx.fillStyle = "#4c372b";
      ctx.fillRect(x * 32, y * 32 + 30, 32, 4);
      for (const dx of [3, 27]) {
        ctx.fillRect(x * 32 + dx, y * 32 + 29, 3, 10);
        ctx.fillStyle = "#ba945e";
        ctx.fillRect(x * 32 + dx, y * 32 + 28, 3, 2);
        ctx.fillStyle = "#4c372b";
      }
    }
  });

  VillageView.ripples = [];
  for (let i = 0; i < 80; i++) {
    const x = noise(i, 8) * width, y = noise(i, 19) * height;
    const tx = Math.floor(x / 32), ty = Math.floor(y / 32);
    const open = [-2, -1, 0, 1, 2].every((dx) => [-1, 0, 1].every((dy) => terrainAt(tx + dx, ty + dy) === "water"));
    if (open) VillageView.ripples.push({ x, y, phase: noise(i, 3) * Math.PI * 2, length: 14 + noise(i, 4) * 22 });
  }
  return canvas;
}

function placeOf(id) {
  return VillageView.sim?.places?.find((place) => place.id === id) || null;
}

function ground(place) {
  return { x: (place.x + 0.5) * 32, y: (place.y + 0.5) * 32 };
}

function band(value, full) {
  if (value <= 0) return "empty";
  if (value < full) return "half";
  return "full";
}

function buildingFile(item) {
  const sim = VillageView.sim;
  const pantry = sim?.pantry || {};
  const step = sim?.step || 0;
  let state = "ready";
  if (item.id === "fire") state = pantry.hearth_lit ? "lit" : "resting";
  else if (item.id === "shore") state = pantry.boat_condition === "leaking" ? "leaking" : "sound";
  else if (item.id === "nets") state = pantry.net_condition === "torn" ? "torn" : "sound";
  else if (item.id === "well" || item.id === "rain_barrel") {
    const source = sim?.waterSources?.[item.id];
    state = source?.fouled_until > step ? "fouled" : source && source.supply <= 0 ? "empty" : "ready";
  }
  return item.states[state] || item.file;
}

function resourceFile(item) {
  const sim = VillageView.sim;
  const pantry = sim?.pantry || {};
  const population = Math.max(1, sim?.population || 1);
  let state = "full";
  if (item.id === "fish") state = band(pantry.fish || 0, population);
  else if (item.id === "herbs") state = band(pantry.herbs || 0, Math.max(1, Math.ceil(population / 3)));
  else if (item.id === "water") state = band(pantry.water || 0, sim?.centers?.water?.daily_need || population);
  else if (item.id === "pantry") {
    const food = sim?.centers?.food;
    if (!food) state = band((pantry.fish || 0) + (pantry.herbs || 0), 1);
    else state = food.level <= 0 ? "empty" : food.low ? "half" : "full";
  }
  return item.states[state] || item.file;
}

function spriteFile(el, now) {
  const people = VillageView.manifest.characters;
  const person = people[el.dataset.actor] || people.visitor;
  if (!person) return null;
  let action = el.dataset.action || "idle";
  if (action === "walk" && !VillageView.reduced && Math.floor(now / 220) % 2 === 1) action = "idle";
  const dir = el.dataset.dir || "s";
  return person.actions[action]?.[dir] || person.actions.idle?.[dir] || person.actions.idle.s;
}

function actorFoot(el, inner) {
  const style = getComputedStyle(el);
  const left = parseFloat(style.left);
  const top = parseFloat(style.top);
  if (!Number.isFinite(left) || !Number.isFinite(top) || !inner.clientWidth || !inner.clientHeight) return null;
  return {
    x: left / inner.clientWidth * 1280,
    y: top / inner.clientHeight * 720,
  };
}

function shadow(ctx, x, y, rx) {
  const shade = ctx.createRadialGradient(x, y, 1, x, y, rx);
  shade.addColorStop(0, "rgba(10, 27, 27, 0.36)");
  shade.addColorStop(1, "rgba(10, 27, 27, 0)");
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(1, 0.35);
  ctx.translate(-x, -y);
  ctx.fillStyle = shade;
  ctx.beginPath();
  ctx.arc(x, y, rx, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function castShadow(ctx, item, image) {
  let silhouette = VillageView.silhouettes.get(item.file);
  if (!silhouette) {
    silhouette = surface(image.width, image.height);
    const shade = silhouette.getContext("2d");
    shade.drawImage(image, 0, 0);
    shade.globalCompositeOperation = "source-in";
    shade.fillStyle = "#142c31";
    shade.fillRect(0, 0, image.width, image.height);
    VillageView.silhouettes.set(item.file, silhouette);
  }
  ctx.save();
  ctx.globalAlpha = 0.16;
  ctx.translate(item.x, item.gy);
  ctx.transform(1, 0, -0.42, -0.28, 0, 0);
  ctx.drawImage(silhouette, -item.anchor[0], -item.anchor[1]);
  ctx.restore();
}

function drawWater(ctx, seconds) {
  for (const ripple of VillageView.ripples) {
    const wave = Math.sin(seconds * 0.7 + ripple.phase);
    const x = Math.round(ripple.x + wave * 2), y = Math.round(ripple.y);
    const length = Math.round(ripple.length);
    ctx.fillStyle = "rgba(8, 32, 44, 0.22)";
    ctx.fillRect(x, y + 1, length, 1);
    ctx.fillStyle = `rgba(198, 230, 220, ${0.16 + (wave + 1) * 0.08})`;
    ctx.fillRect(x, y, length, 1);
  }
  // A few long glints on open water, not a field of specks.
  const minute = VillageView.sim?.minute ?? 1080;
  const night = minute < 360 || minute >= 1200;
  for (let i = 0; i < 14; i++) {
    const y = 50 + i * 42;
    const x = 980 + Math.sin(i * 1.7 + seconds * 0.45) * (18 + i);
    if (terrainAt(Math.floor(x / 32), Math.floor(y / 32)) !== "water"
      || terrainAt(Math.floor(x / 32) - 2, Math.floor(y / 32)) !== "water") continue;
    ctx.fillStyle = night ? "rgba(186,214,224,0.16)" : "rgba(255,236,190,0.20)";
    ctx.fillRect(Math.round(x), y, 22 + noise(i, 25) * 26, 1);
  }
}

const lanternSites = [
  ["guest_home", 33, 4], ["mara_home", 33, 5], ["tom_home", -32, 5],
  ["lin_home", 34, 4], ["garden", 33, 5], ["water_center", 32, 0],
];

function lanterns() {
  return lanternSites.flatMap(([id, dx, dy]) => {
    const place = placeOf(id);
    if (!place) return [];
    const p = ground(place);
    return [{ x: p.x + dx, y: p.y + dy }];
  });
}

function drawLantern(ctx, x, y, lit) {
  shadow(ctx, x, y, 7);
  ctx.fillStyle = "#473d30";
  ctx.fillRect(x - 1, y - 25, 3, 25);
  ctx.fillStyle = "#a98b59";
  ctx.fillRect(x - 1, y - 24, 1, 23);
  ctx.fillStyle = "#302f28";
  ctx.fillRect(x - 4, y - 29, 9, 3);
  ctx.fillRect(x - 3, y - 26, 7, 9);
  ctx.fillStyle = lit ? "#ffc474" : "#6a6256";
  ctx.fillRect(x - 2, y - 25, 5, 6);
  if (lit) {
    ctx.fillStyle = "#ffe9b0";
    ctx.fillRect(x - 1, y - 24, 2, 4);
  }
}

// The painted tiles are an evening scene. Multiply can only darken them,
// so daytime gets a separate lift and the post lanterns stay dark until dusk.
function sunFactor(minute) {
  if (minute < 420 || minute >= 1020) return 0;
  if (minute < 600) return (minute - 420) / 180;
  if (minute < 840) return 1;
  return (1020 - minute) / 180;
}

function lampStrength(minute) {
  if (minute < 300 || minute >= 1140) return 1;
  if (minute < 420) return (420 - minute) / 120;
  if (minute < 1020) return 0;
  return (minute - 1020) / 120;
}

function glow(ctx, x, y, radius, strength) {
  const gradient = ctx.createRadialGradient(x, y, 1, x, y, radius);
  gradient.addColorStop(0, `rgba(255,196,95,${strength})`);
  gradient.addColorStop(0.25, `rgba(241,159,62,${strength * 0.40})`);
  gradient.addColorStop(1, "rgba(229,131,47,0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
}

function drawLighting(ctx, seconds, lamps) {
  const minute = VillageView.sim?.minute ?? 1080;
  const stops = [
    [0, 128, 158, 199], [300, 128, 158, 199], [420, 233, 220, 199],
    [720, 255, 253, 239], [960, 255, 239, 211], [1080, 242, 210, 179],
    [1200, 163, 177, 207], [1320, 128, 158, 199], [1440, 128, 158, 199],
  ];
  const right = stops.findIndex((stop) => stop[0] > minute);
  const a = stops[Math.max(0, right - 1)], b = stops[right < 0 ? stops.length - 1 : right];
  const blend = a === b ? 0 : Math.max(0, Math.min(1, (minute - a[0]) / (b[0] - a[0])));
  const color = a.slice(1).map((channel, i) => Math.round(channel + (b[i + 1] - channel) * blend));
  const sun = sunFactor(minute);
  const lampsOn = lampStrength(minute);
  ctx.save();
  ctx.globalCompositeOperation = "multiply";
  ctx.fillStyle = `rgb(${color.join(",")})`;
  ctx.fillRect(0, 0, 1280, 720);
  if (VillageView.sim?.weather === "storm") {
    ctx.fillStyle = "#a5b8c5";
    ctx.fillRect(0, 0, 1280, 720);
  }
  if (sun > 0) {
    ctx.globalCompositeOperation = "screen";
    ctx.fillStyle = `rgba(255, 244, 214, ${0.10 + sun * 0.34})`;
    ctx.fillRect(0, 0, 1280, 720);
  }
  ctx.globalCompositeOperation = "screen";
  for (let i = 0; i < lamps.length; i++) {
    const { x, y } = lamps[i];
    glow(ctx, x, y - 22, 42, lampsOn * (0.42 + Math.sin(seconds * 2 + i) * 0.025));
    glow(ctx, x, y + 1, 31, lampsOn * 0.16);
  }
  for (const place of VillageView.sim?.places || []) {
    if (!place.id.endsWith("_home")) continue;
    const p = ground(place);
    glow(ctx, p.x - 9, p.y - 21, 45, lampsOn * 0.27);
  }
  const fire = placeOf("fire");
  if (fire && VillageView.sim?.pantry.hearth_lit) {
    const p = ground(fire);
    glow(ctx, p.x, p.y - 8, 65, 0.22 + lampsOn * 0.40 + Math.sin(seconds * 5) * 0.025);
  }
  ctx.restore();
  const vignette = ctx.createRadialGradient(640, 340, 260, 640, 360, 760);
  vignette.addColorStop(0, "rgba(7,27,39,0)");
  vignette.addColorStop(1, "rgba(7,27,39,0.23)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, 1280, 720);
}

function drawSmoke(ctx, seconds) {
  if (VillageView.reduced) return;
  for (const place of VillageView.sim?.places || []) {
    if (!place.id.endsWith("_home")) continue;
    const p = ground(place);
    const guest = place.id === "guest_home";
    for (let i = 0; i < 4; i++) {
      const age = (seconds * 0.16 + i / 4 + place.x * 0.1) % 1;
      const x = p.x + (guest ? 12 : 17) + age * 14 + Math.sin(age * 5 + i) * 3;
      const y = p.y - (guest ? 56 : 65) - age * 35;
      ctx.fillStyle = `rgba(216,220,204,${Math.sin(age * Math.PI) * 0.13})`;
      ctx.beginPath();
      ctx.ellipse(x, y, 2 + age * 6, 2 + age * 4, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  const fire = placeOf("fire");
  if (fire && VillageView.sim?.pantry.hearth_lit) {
    const p = ground(fire);
    for (let i = 0; i < 5; i++) {
      const age = (seconds * 0.35 + i / 5) % 1;
      ctx.fillStyle = `rgba(255,202,106,${(1 - age) * 0.8})`;
      ctx.fillRect(Math.round(p.x + Math.sin(i * 3 + age * 4) * age * 9), Math.round(p.y - 10 - age * 30), 1, 2);
    }
  }
}

function drawWorld(now) {
  const canvas = document.getElementById("world");
  const inner = document.getElementById("scene-inner");
  if (!canvas || !inner || !VillageView.ready) return;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const seconds = VillageView.reduced ? 0 : now / 1000;
  ctx.drawImage(VillageView.terrain[0], 0, 0);
  drawWater(ctx, seconds);
  const manifest = VillageView.manifest;
  const items = [];
  for (const item of manifest.scenery) {
    const [tx, ty] = item.tile;
    items.push({ y: (ty + 0.5) * 32, z: 0, file: item.file, x: (tx + 0.5) * 32, gy: (ty + 0.5) * 32, anchor: item.anchor, shade: 8 });
  }
  for (const item of manifest.buildings) {
    const place = placeOf(item.id);
    if (!place) continue;
    const point = ground(place);
    items.push({ y: point.y, z: 1, file: buildingFile(item), x: point.x, gy: point.y, anchor: item.anchor, shade: 22 });
  }
  for (const item of manifest.resources) {
    const place = placeOf(item.place);
    if (!place) continue;
    const point = ground(place);
    items.push({ y: point.y, z: 2, file: resourceFile(item), x: point.x, gy: point.y, anchor: item.anchor, shade: 8 });
  }
  for (const el of document.querySelectorAll("#villagers .sprite, .festival-visitor")) {
    const point = actorFoot(el, inner);
    const file = spriteFile(el, now);
    if (!point || !file) continue;
    const person = manifest.characters[el.dataset.actor] || manifest.characters.visitor;
    items.push({
      y: point.y,
      z: 3,
      file,
      x: point.x,
      gy: point.y,
      anchor: person.anchor,
      shade: el.dataset.action === "collapsed" ? 11 : 6,
    });
  }
  const lamps = lanterns();
  for (const lamp of lamps) items.push({ y: lamp.y, z: 1, lamp });
  items.sort((a, b) => a.y - b.y || a.z - b.z);
  // All cast shadows lie on the ground, below people and buildings.
  for (const item of items) {
    const image = VillageView.images.get(item.file);
    if (image && item.z < 2) castShadow(ctx, item, image);
  }
  for (const item of items) {
    if (item.lamp) {
      drawLantern(ctx, item.lamp.x, item.lamp.y, lampStrength(VillageView.sim?.minute ?? 1080) > 0.2);
      continue;
    }
    const image = VillageView.images.get(item.file);
    if (!image) continue;
    shadow(ctx, item.x, item.gy, item.shade);
    ctx.drawImage(image, Math.round(item.x - item.anchor[0]), Math.round(item.gy - item.anchor[1]));
  }
  drawSmoke(ctx, seconds);
  drawLighting(ctx, seconds, lamps);
}

function frame(now) {
  requestAnimationFrame(frame);
  if (!VillageView.ready || document.hidden) return;
  if (now - (frame.last || 0) < 40) return;
  frame.last = now;
  drawWorld(now);
}

VillageView.start = async () => {
  try {
    const response = await fetch("/assets/manifest.json");
    if (!response.ok) return;
    const manifest = await response.json();
    const entries = await Promise.all([...assetFiles(manifest)].map(async (file) => {
      const image = await loadImage(`/assets/${file}`);
      return [file, image];
    }));
    VillageView.images = new Map(entries);
    VillageView.manifest = manifest;
    const terrain = bakeTerrain(manifest);
    VillageView.terrain = [terrain, terrain];
    VillageView.ready = true;
    document.getElementById("scene")?.classList.add("asset-view");
    document.dispatchEvent(new CustomEvent("village-art"));
    drawWorld(performance.now());
  } catch (error) {
    console.warn(error.message);
  }
};

requestAnimationFrame(frame);
VillageView.start();
