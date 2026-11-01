"use strict";

// A small shore, hearth, and weather bed. Stingers only play when the clock
// moves forward one step, so scrubbing a recording does not ring the bell.
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
  let beds = { storm: null, hearth: null };
  let hearthTimer = null;
  let dripTimer = null;

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

  function tone(frequency, when, seconds, peak) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(peak, when + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + seconds);
    osc.connect(gain);
    gain.connect(master);
    osc.start(when);
    osc.stop(when + seconds + 0.05);
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
    if (!on) return;
    const drip = () => {
      if (!enabled || !beds.storm) return;
      if (!document.hidden) tone(900 + Math.random() * 800, ctx.currentTime, 0.18, 0.012);
      dripTimer = setTimeout(drip, document.hidden ? 1500 : 600 + Math.random() * 1600);
    };
    dripTimer = setTimeout(drip, 500);
  }

  function bits(sim) {
    const hour = Math.floor((sim.minute || 0) / 60);
    return {
      step: sim.step,
      minute: sim.minute,
      storm: sim.weather === "storm",
      festival: Boolean(sim.festival),
      hearth: Boolean(sim.pantry?.hearth_lit) && (hour >= 18 || hour < 6),
    };
  }

  function applyBeds(sim) {
    const next = bits(sim);
    if (next.storm !== beds.storm) {
      beds.storm = next.storm;
      setRain(next.storm);
    }
    if (next.hearth !== beds.hearth) {
      beds.hearth = next.hearth;
      setHearth(next.hearth);
    }
  }

  function stingers(before, after) {
    if (!before.storm && after.storm) burst(1.3, 240, 0.08);
    if (!before.festival && after.festival) {
      [349.23, 440, 523.25].forEach((frequency, index) => {
        tone(frequency, ctx.currentTime + index * 0.16, 1.5, 0.04);
      });
    }
    if (before.minute < 1080 && after.minute >= 1080 && after.minute < 1110) {
      tone(523.25, ctx.currentTime, 1.8, 0.035);
      tone(784, ctx.currentTime + 0.08, 1.6, 0.018);
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
    beds = { storm: null, hearth: null };
    hearthTimer = stopTimer(hearthTimer);
    dripTimer = stopTimer(dripTimer);
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
    beds = { storm: null, hearth: null };
    if (latest) applyBeds(latest);
  }

  function follow(sim) {
    if (!sim) return;
    const next = bits(sim);
    latest = sim;
    if (enabled && ctx && ctx.state === "running") {
      applyBeds(sim);
      if (previous && next.step === previous.step + 1) stingers(previous, next);
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

  window.villageSound = { follow, toggle };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
