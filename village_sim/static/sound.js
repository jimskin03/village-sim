"use strict";

// Shore, hearth, and weather beds, plus a short theme for each operator event.
// A theme starts when that event begins on the live clock or the next step.
// Scrubbing a recording does not replay it.
(function () {
  const KEY = "lantern-bay-sound";
  let ctx;
  let master;
  let noise;
  let shoreGain;
  let rainGain;
  let enabled = false;
  let latest = null;
  let previous = null;
  let beds = { storm: null, hearth: null, festival: null, pirate: null, calm: null };
  const pads = { storm: null, festival: null, pirate: null, calm: null };
  let hearthTimer = null;
  let dripTimer = null;
  let pirateTimer = null;

  function remember(on) {
    try {
      localStorage.setItem(KEY, on ? "on" : "off");
    } catch {
      /* Private browsing can refuse storage. The button still works. */
    }
  }

  function storedOn() {
    try {
      return localStorage.getItem(KEY) === "on";
    } catch {
      return false;
    }
  }

  function makeNoise() {
    const length = ctx.sampleRate * 3;
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let brown = 0;
    for (let i = 0; i < length; i++) {
      brown = (brown + 0.02 * (Math.random() * 2 - 1)) / 1.02;
      data[i] = brown;
    }
    let peak = 0;
    for (let i = 0; i < length; i++) peak = Math.max(peak, Math.abs(data[i]));
    const scale = peak ? 0.9 / peak : 1;
    for (let i = 0; i < length; i++) data[i] *= scale;
    return buffer;
  }

  function loop(filterType, frequency) {
    const source = ctx.createBufferSource();
    source.buffer = noise;
    source.loop = true;
    const filter = ctx.createBiquadFilter();
    filter.type = filterType;
    filter.frequency.value = frequency;
    const gain = ctx.createGain();
    gain.gain.value = 0.0001;
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    source.start();
    return gain;
  }

  function ensure() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return null;
    if (ctx) return ctx;
    ctx = new AudioCtx();
    master = ctx.createGain();
    master.gain.value = 0.0001;
    master.connect(ctx.destination);
    noise = makeNoise();
    shoreGain = loop("lowpass", 480);
    rainGain = loop("highpass", 700);
    const tide = ctx.createOscillator();
    tide.frequency.value = 0.07;
    const depth = ctx.createGain();
    depth.gain.value = 0.016;
    tide.connect(depth);
    depth.connect(shoreGain.gain);
    tide.start();
    shoreGain.gain.value = 0.042;
    return ctx;
  }

  function tone(frequency, when, seconds, peak, type = "sine", cutoff = 0) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), when + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + Math.max(seconds, 0.05));
    osc.connect(gain);
    let output = gain;
    if (cutoff) {
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = cutoff;
      gain.connect(filter);
      output = filter;
    }
    output.connect(master);
    osc.start(when);
    osc.stop(when + seconds + 0.05);
  }

  function chord(frequencies, type, peak, cutoff) {
    const now = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.linearRampToValueAtTime(peak, now + 0.7);
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = cutoff;
    gain.connect(filter);
    filter.connect(master);
    const oscillators = frequencies.map((frequency) => {
      const osc = ctx.createOscillator();
      osc.type = type;
      osc.frequency.value = frequency;
      osc.connect(gain);
      osc.start(now);
      return osc;
    });
    let stopped = false;
    return {
      stop(seconds = 0.9) {
        if (stopped) return;
        stopped = true;
        const time = ctx.currentTime;
        gain.gain.cancelScheduledValues(time);
        gain.gain.linearRampToValueAtTime(0.0001, time + seconds);
        for (const osc of oscillators) {
          try {
            osc.stop(time + seconds + 0.05);
          } catch {
            /* The voice already ended. */
          }
        }
      },
    };
  }

  function play(notes, type, peak, cutoff = 0) {
    const now = ctx.currentTime;
    for (const [frequency, at, seconds] of notes) tone(frequency, now + at, seconds, peak, type, cutoff);
  }

  function burst(seconds, frequency, peak) {
    const now = ctx.currentTime;
    const source = ctx.createBufferSource();
    source.buffer = noise;
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(frequency, now);
    filter.frequency.exponentialRampToValueAtTime(90, now + seconds);
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(peak, now + 0.04);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    source.start(now);
    source.stop(now + seconds + 0.05);
  }

  function crackle() {
    if (!ctx || document.hidden) return;
    const now = ctx.currentTime;
    const source = ctx.createBufferSource();
    source.buffer = noise;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = 500 + Math.random() * 1600;
    filter.Q.value = 0.6;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.02 + Math.random() * 0.025, now + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.09);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    source.start(now);
    source.stop(now + 0.12);
  }

  function stopTimer(timer) {
    if (timer) clearTimeout(timer);
    return null;
  }

  function setHearth(on) {
    hearthTimer = stopTimer(hearthTimer);
    if (!on) return;
    const pop = () => {
      if (!enabled || !beds.hearth) return;
      if (!document.hidden) crackle();
      hearthTimer = setTimeout(pop, document.hidden ? 1200 : 220 + Math.random() * 900);
    };
    hearthTimer = setTimeout(pop, 200);
  }

  function setRain(on) {
    const now = ctx.currentTime;
    rainGain.gain.cancelScheduledValues(now);
    rainGain.gain.linearRampToValueAtTime(on ? 0.045 : 0.0001, now + (on ? 1.2 : 1.6));
    dripTimer = stopTimer(dripTimer);
    if (pads.storm) {
      pads.storm.stop();
      pads.storm = null;
    }
    if (!on) return;
    pads.storm = chord([73.42, 110], "triangle", 0.02, 320);
    const drip = () => {
      if (!enabled || !beds.storm) return;
      if (!document.hidden) tone(900 + Math.random() * 800, ctx.currentTime, 0.18, 0.012);
      dripTimer = setTimeout(drip, document.hidden ? 1500 : 600 + Math.random() * 1600);
    };
    dripTimer = setTimeout(drip, 500);
  }

  function setFestival(on) {
    if (pads.festival) {
      pads.festival.stop();
      pads.festival = null;
    }
    if (on) pads.festival = chord([174.61, 261.63], "sine", 0.016, 1400);
  }

  function setPirate(on) {
    pirateTimer = stopTimer(pirateTimer);
    if (pads.pirate) {
      pads.pirate.stop();
      pads.pirate = null;
    }
    if (!on) return;
    pads.pirate = chord([73.42], "sine", 0.018, 240);
    const beat = () => {
      if (!enabled || !beds.pirate) return;
      if (!document.hidden && ctx) {
        burst(0.16, 130, 0.04);
        tone(146.83, ctx.currentTime, 0.1, 0.02, "square", 700);
      }
      pirateTimer = setTimeout(beat, document.hidden ? 1500 : 560);
    };
    pirateTimer = setTimeout(beat, 1700);
  }

  function setCalm(on) {
    if (pads.calm) {
      pads.calm.stop();
      pads.calm = null;
    }
    if (on) pads.calm = chord([220, 329.63], "sine", 0.014, 1600);
  }

  function freshBeds() {
    return { storm: null, hearth: null, festival: null, pirate: null, calm: null };
  }

  function stopPads(seconds) {
    pirateTimer = stopTimer(pirateTimer);
    for (const name of Object.keys(pads)) {
      if (pads[name]) pads[name].stop(seconds);
      pads[name] = null;
    }
  }

  function bits(sim) {
    const hour = Math.floor((sim.minute || 0) / 60);
    return {
      step: sim.step,
      minute: sim.minute,
      storm: sim.weather === "storm",
      festival: Boolean(sim.festival),
      pirate: Boolean(sim.pirate),
      calm: Boolean(sim.calm),
      hearth: Boolean(sim.pantry?.hearth_lit) && (hour >= 18 || hour < 6),
    };
  }

  // Live inject keeps the step. Playback advances one step. A scrub jumps.
  function cues(before, after) {
    if (!before || !after) return [];
    const live = after.step === before.step || after.step === before.step + 1;
    const names = [];
    if (live && !before.storm && after.storm) names.push("storm");
    if (live && !before.festival && after.festival) names.push("festival");
    if (live && !before.pirate && after.pirate) names.push("pirate");
    if (live && !before.calm && after.calm) names.push("calm");
    if (
      after.step === before.step + 1
      && before.minute < 1080
      && after.minute >= 1080
      && after.minute < 1110
    ) names.push("supper");
    return names;
  }

  function stormTheme() {
    burst(1.3, 240, 0.07);
    play([
      [73.42, 0.05, 1.8],
      [87.31, 0.75, 1.6],
      [98, 1.45, 1.5],
      [73.42, 2.2, 2.4],
    ], "triangle", 0.036);
  }

  function festivalTheme() {
    play([
      [349.23, 0, 1.05],
      [440, 0.32, 1.05],
      [523.25, 0.64, 1.15],
      [659.25, 1.08, 1.15],
      [523.25, 1.5, 1.05],
      [440, 1.86, 1.05],
      [349.23, 2.24, 1.2],
      [392, 2.72, 1.15],
      [523.25, 3.16, 1.6],
    ], "triangle", 0.038);
  }

  function pirateTheme() {
    burst(0.4, 150, 0.08);
    play([
      [146.83, 0.12, 0.26],
      [174.61, 0.34, 0.26],
      [220, 0.56, 0.3],
      [174.61, 0.82, 0.26],
      [146.83, 1.04, 0.26],
      [130.81, 1.28, 0.32],
      [110, 1.58, 0.4],
      [146.83, 1.96, 0.55],
    ], "square", 0.03, 880);
  }

  function calmTheme() {
    play([
      [329.63, 0, 1.6],
      [293.66, 0.5, 1.6],
      [246.94, 1.0, 1.6],
      [220, 1.5, 1.7],
      [246.94, 2.15, 1.5],
      [293.66, 2.65, 1.5],
      [329.63, 3.15, 1.7],
      [392, 3.75, 2.2],
    ], "sine", 0.026);
  }

  function playThemes(before, after) {
    for (const name of cues(before, after)) {
      if (name === "storm") stormTheme();
      else if (name === "festival") festivalTheme();
      else if (name === "pirate") pirateTheme();
      else if (name === "calm") calmTheme();
      else if (name === "supper") {
        tone(523.25, ctx.currentTime, 1.8, 0.035);
        tone(784, ctx.currentTime + 0.08, 1.6, 0.018);
      }
    }
  }

  function applyBeds(next) {
    if (next.storm !== beds.storm) {
      beds.storm = next.storm;
      setRain(next.storm);
    }
    if (next.hearth !== beds.hearth) {
      beds.hearth = next.hearth;
      setHearth(next.hearth);
    }
    if (next.festival !== beds.festival) {
      beds.festival = next.festival;
      setFestival(next.festival);
    }
    if (next.pirate !== beds.pirate) {
      beds.pirate = next.pirate;
      setPirate(next.pirate);
    }
    if (next.calm !== beds.calm) {
      beds.calm = next.calm;
      setCalm(next.calm);
    }
  }

  function paint() {
    const button = document.querySelector("#sound");
    if (!button) return;
    const label = enabled ? "Mute village sounds" : "Play village sounds";
    button.setAttribute("aria-pressed", enabled ? "true" : "false");
    button.title = label;
    button.setAttribute("aria-label", label);
    button.innerHTML = `<svg aria-hidden="true"><use href="#i-${enabled ? "sound" : "muted"}"/></svg><span>Sound ${enabled ? "on" : "off"}</span>`;
  }

  function silence() {
    beds = freshBeds();
    hearthTimer = stopTimer(hearthTimer);
    dripTimer = stopTimer(dripTimer);
    stopPads(0.25);
    if (!ctx) return;
    const now = ctx.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.linearRampToValueAtTime(0.0001, now + 0.25);
    if (rainGain) rainGain.gain.setValueAtTime(0.0001, now);
  }

  async function start() {
    const audio = ensure();
    if (!audio) return;
    await audio.resume();
    const now = audio.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.linearRampToValueAtTime(0.9, now + 0.45);
    stopPads(0.2);
    beds = freshBeds();
    if (latest) applyBeds(bits(latest));
  }

  function follow(sim) {
    if (!sim) return;
    const next = bits(sim);
    latest = sim;
    if (enabled && ctx && ctx.state === "running") {
      applyBeds(next);
      playThemes(previous, next);
    }
    previous = next;
  }

  function toggle() {
    enabled = !enabled;
    remember(enabled);
    paint();
    if (enabled) start();
    else silence();
  }

  function bind() {
    const button = document.querySelector("#sound");
    if (!button) return;
    enabled = storedOn();
    paint();
    button.addEventListener("click", toggle);
    if (!enabled) return;
    const resume = (event) => {
      if (event.target.closest && event.target.closest("#sound")) return;
      document.removeEventListener("pointerdown", resume, true);
      if (enabled) start();
    };
    document.addEventListener("pointerdown", resume, true);
  }

  window.villageSound = { follow, toggle, cues };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
