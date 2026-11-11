import { DATA } from "./data.js";
import {
  Keyboard, codeName, control, hidSupported, loadState, parseMacroEvents, saveState, targetCode,
} from "./protocol.js";
import { Analyzer, BANDS, MusicRenderer, REACTIVE, pattern, previewFrame } from "./effects.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const st = loadState();
let kb = null;
let queue = Promise.resolve();

const LEGEND = Object.fromEntries(DATA.keys.map((k) => [k.code, k.label]));
const LED_OF = Object.fromEntries(DATA.keys.map((k) => [k.code, k.led]));
const NAMED = {
  red: [255, 40, 60], orange: [255, 128, 0], yellow: [255, 220, 0], green: [0, 230, 110],
  cyan: [0, 220, 255], blue: [40, 90, 255], purple: [150, 70, 255], pink: [255, 80, 180],
  white: [255, 255, 255], off: [0, 0, 0],
};
const PATTERN_LABELS = {
  solid: "Solid fill", "rainbow-horizontal": "Rainbow across", "rainbow-vertical": "Rainbow down",
  "rainbow-diagonal": "Rainbow diagonal", "gradient-horizontal": "Gradient across",
  "gradient-vertical": "Gradient down", "highlight-gaming": "WASD highlight", random: "Random",
};
const QUICK_TARGETS = ["esc", "capslock", "lctrl", "backspace", "delete", "volup", "voldown",
  "mute", "playpause", "next", "prev", "calculator", "mycomputer", "f13"];
const TABS = {
  light: ["Lighting", "Pick an effect. Changes apply to the keyboard right away."],
  rgb: ["Per-key RGB", "Paint keys, fill patterns and save your own designs."],
  remap: ["Remap", "Click a key on the board, then choose what it should do."],
  macro: ["Macros", "Click a key, type a key sequence and store it on the keyboard."],
  music: ["Music sync", "Lights that move with whatever is playing."],
  settings: ["Settings", "Sleep timer, response time and housekeeping."],
};
const HINTS = {
  light: "Live preview, approximate", rgb: "Click or drag to paint, ctrl+z to undo",
  remap: "Click the key you want to change", macro: "Click the key that should run the macro",
  music: "", settings: "",
};

// -- helpers -----------------------------------------------------------------
const hex = (rgb) => "#" + rgb.map((c) => c.toString(16).padStart(2, "0")).join("");
const fromHex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const ARROW_WORDS = { "↑": "Up", "↓": "Down", "←": "Left", "→": "Right" };
const keyLabel = (layer, code) => {
  const legend = LEGEND[code] ?? codeName(code);
  return (layer ? "Fn+" : "") + (ARROW_WORDS[legend] ?? legend);
};
const icon = (id, size = "sm") => `<svg class="i ${size}"><use href="#i-${id}"/></svg>`;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

function toast(msg, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = icon(kind === "ok" ? "check" : "alert") + "<span></span>";
  el.lastChild.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 2800);
}

function setSync(text, cls = "") {
  const el = $("#sync");
  el.lastElementChild.textContent = text;
  el.className = "sync " + cls;
}

function log(line) {
  const pre = $("#log");
  pre.textContent = (pre.textContent + line + "\n").split("\n").slice(-300).join("\n");
  pre.scrollTop = pre.scrollHeight;
}

function confirmDialog(title, text) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  dlg.showModal();
  return new Promise((r) => dlg.addEventListener("close", () => r(dlg.returnValue === "yes"), { once: true }));
}

// One keyboard operation at a time; state is saved when it worked.
function run(label, fn, { after, quiet = false } = {}) {
  if (!kb) {
    if (!quiet) toast("Connect the keyboard first, or try the demo.", "warn");
    else setSync("Preview only");
    return Promise.resolve();
  }
  setSync("Saving…");
  queue = queue.then(async () => {
    try {
      await fn();
      saveState(st);
      setSync(kb.simulated ? "Demo, nothing sent" : "Saved", "ok");
      if (!quiet) toast(label);
      after?.();
    } catch (e) {
      setSync(e.message, "err");
      toast(`${label}: ${e.message}`, "error");
    }
  });
  return queue;
}

function seg(id, onPick) {
  const box = $("#" + id);
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || box.getAttribute("aria-disabled") === "true") return;
    setSeg(id, b.dataset.v ?? b.dataset.tool);
    onPick(b.dataset.v ?? b.dataset.tool);
  });
}
function setSeg(id, v) {
  $$(`#${id} button`).forEach((b) => b.classList.toggle("active", String(b.dataset.v ?? b.dataset.tool) === String(v)));
}
function segValue(id) {
  const b = $(`#${id} button.active`);
  return b ? b.dataset.v ?? b.dataset.tool : null;
}
function rangeFill(input) {
  const p = ((input.value - input.min) / (input.max - input.min)) * 100;
  input.style.setProperty("--p", p + "%");
  const out = input.parentElement.querySelector("output");
  if (out) out.textContent = input.value;
}

// -- keyboard rendering -----------------------------------------------------------
// Board bounds from the key rectangles, with room for the case around them.
const VIEW = (() => {
  const xs = DATA.keys.flatMap(({ rect: [x, , w] }) => [x, x + w]);
  const ys = DATA.keys.flatMap(({ rect: [, y, , h] }) => [y, y + h]);
  const pad = 14;
  const x = Math.min(...xs) - pad, y = Math.min(...ys) - pad;
  return { x, y, w: Math.max(...xs) + pad - x, h: Math.max(...ys) + pad - y };
})();

function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function drawBoard(canvas, colors, { marks = {}, labels = 0.85, glow = true, plate = true } = {}) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(canvas.clientWidth * dpr), H = Math.round(canvas.clientHeight * dpr);
  if (!W || !H) return;
  if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
  const s = Math.min(W / VIEW.w, H / VIEW.h);
  const ox = (W - VIEW.w * s) / 2 - VIEW.x * s, oy = (H - VIEW.h * s) / 2 - VIEW.y * s;
  ctx.clearRect(0, 0, W, H);

  if (plate) {  // case + plate
    const g = ctx.createLinearGradient(0, oy + VIEW.y * s, 0, oy + (VIEW.y + VIEW.h) * s);
    g.addColorStop(0, "#1a1c24");
    g.addColorStop(1, "#0e0f14");
    ctx.fillStyle = g;
    rr(ctx, ox + (VIEW.x + 2) * s, oy + (VIEW.y + 2) * s, (VIEW.w - 4) * s, (VIEW.h - 4) * s, 14 * s);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth = 1.2 * s;
    ctx.stroke();
  }

  const lit = [];
  for (const k of DATA.keys) {
    const rgb = colors[k.led] || [0, 0, 0];
    lit.push([k, rgb, Math.max(...rgb) / 255]);
  }
  if (glow) {  // under-glow on the plate
    ctx.save();
    for (const [k, rgb, v] of lit) {
      if (v < 0.04) continue;
      const [x, y, w, h] = k.rect;
      ctx.shadowColor = `rgba(${rgb},${0.85 * v})`;
      ctx.shadowBlur = 16 * s;
      ctx.fillStyle = `rgba(${rgb},${0.55 * v})`;
      rr(ctx, ox + (x + 2) * s, oy + (y + 2) * s, (w - 7) * s, (h - 7) * s, 5 * s);
      ctx.fill();
    }
    ctx.restore();
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const [k, rgb, v] of lit) {
    const [x, y, w, h] = k.rect;
    const px = ox + x * s, py = oy + y * s, pw = (w - 3) * s, ph = (h - 3) * s;
    // skirt
    ctx.fillStyle = "#14161d";
    rr(ctx, px, py, pw, ph, 5 * s);
    ctx.fill();
    // top face
    const tx = px + 2.6 * s, ty = py + 1.8 * s, tw = pw - 5.2 * s, th = ph - 6 * s;
    const g = ctx.createLinearGradient(0, ty, 0, ty + th);
    g.addColorStop(0, "#30343f");
    g.addColorStop(1, "#23262f");
    ctx.fillStyle = g;
    rr(ctx, tx, ty, tw, th, 4 * s);
    ctx.fill();
    if (v > 0.02) {  // light shining through the cap
      ctx.fillStyle = `rgba(${rgb},${0.18 + 0.5 * v})`;
      ctx.fill();
    }
    const mark = marks[k.led];
    if (mark) {
      ctx.lineWidth = 2.2 * s;
      ctx.strokeStyle = { picked: "#34d399", selected: "#fbbf24", remapped: "#a78bfa", macro: "#38bdf8" }[mark];
      rr(ctx, px - 1.2 * s, py - 1.2 * s, pw + 2.4 * s, ph + 2.4 * s, 6 * s);
      ctx.stroke();
    }
    if (labels) {
      const lum = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114) / 255;
      ctx.globalAlpha = labels;
      ctx.fillStyle = v > 0.02 && lum > 0.55 ? "rgba(10,10,14,0.85)" : "rgba(240,242,248,0.92)";
      ctx.font = `600 ${Math.max(8, (k.label.length > 3 ? 7.6 : 9.4) * s)}px Inter, system-ui, sans-serif`;
      if (ARROW_WORDS[k.label]) drawArrowLegend(ctx, k.label, tx + tw / 2, ty + th / 2, 4.2 * s);
      else ctx.fillText(k.label, tx + tw / 2, ty + th / 2 + 0.4 * s);
      ctx.globalAlpha = 1;
    }
  }
  return { s, ox, oy };
}

// Arrow-key legends as strokes, not text glyphs.
function drawArrowLegend(ctx, legend, cx, cy, r) {
  const angle = { "→": 0, "↓": Math.PI / 2, "←": Math.PI, "↑": -Math.PI / 2 }[legend];
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(angle);
  ctx.strokeStyle = ctx.fillStyle;
  ctx.lineWidth = r * 0.32;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(-r, 0); ctx.lineTo(r, 0);
  ctx.moveTo(r * 0.35, -r * 0.62); ctx.lineTo(r, 0); ctx.lineTo(r * 0.35, r * 0.62);
  ctx.stroke();
  ctx.restore();
}

function keyAt(canvas, e) {
  const r = canvas.getBoundingClientRect();
  const s = Math.min(r.width / VIEW.w, r.height / VIEW.h);
  const ox = (r.width - VIEW.w * s) / 2 - VIEW.x * s, oy = (r.height - VIEW.h * s) / 2 - VIEW.y * s;
  const x = (e.clientX - r.left - ox) / s, y = (e.clientY - r.top - oy) / s;
  return DATA.keys.find(({ rect: [kx, ky, w, h] }) => x >= kx && x <= kx + w && y >= ky && y <= ky + h);
}

// -- stage (the big keyboard) ---------------------------------------------------
const stage = $("#stage");
const ASPECT = `${VIEW.w} / ${VIEW.h}`;
stage.style.aspectRatio = ASPECT;
let dragging = false, lastKey = null;
stage.addEventListener("pointerdown", (e) => {
  const key = keyAt(stage, e);
  dragging = true;
  stage.setPointerCapture(e.pointerId);
  lastKey = key;
  if (key) onStageKey(key, false);
});
stage.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const key = keyAt(stage, e);
  if (key && key !== lastKey) { lastKey = key; onStageKey(key, true); }
});
const endDrag = () => { dragging = false; lastKey = null; };
stage.addEventListener("pointerup", endDrag);
stage.addEventListener("pointercancel", endDrag);

const userPresses = [];  // keys clicked on the board, for reactive effect previews
function onStageKey(key, drag) {
  if (activeTab === "light") {
    userPresses.push([key.led, (performance.now() - t0) / 1000]);
    if (userPresses.length > 12) userPresses.shift();
  } else if (activeTab === "rgb") paintKey(key, drag);
  else if (activeTab === "remap" && !drag) { remapSrc = key.code; refreshRemaps(); }
  else if (activeTab === "macro" && !drag) pickMacroKey(key.code);
}

let musicFrame = {};
function stageContent(t) {
  switch (activeTab) {
    case "rgb":
      return [paint, { marks: Object.fromEntries([...selected].map((l) => [l, "selected"])) }];
    case "remap":
      return [dimmed(), { marks: remapMarks() }];
    case "macro":
      return [dimmed(), { marks: macroMarks() }];
    case "music":
      return [musicFrame, { labels: 0.35 }];
    default:
      return [st.lights_off && activeTab === "settings" ? {} : previewFrame(light, t, st.colors, userPresses), { labels: 0.55 }];
  }
}
const dimmed = () => Object.fromEntries(DATA.keys.map((k) => [k.led, [26, 28, 36]]));

// -- connection ----------------------------------------------------------------
async function attach(next) {
  if (kb && kb !== next) await kb.close();
  kb = next;
  $("#status").className = "status " + (kb.simulated ? "sim" : "on");
  $("#device-name").textContent = kb.simulated ? "Demo mode" : kb.name;
  $("#connect span").textContent = kb.simulated ? "Connect" : "Change";
  $("#welcome").classList.add("hidden");
  setSync(kb.simulated ? "Demo, nothing is sent" : "", "");
  pollStatus();
}

function detach(msg) {
  kb = null;
  $("#status").className = "status";
  $("#device-name").textContent = "Not connected";
  $("#battery").classList.add("hidden");
  $("#connect span").textContent = "Connect";
  $("#welcome").classList.remove("hidden");
  if (msg) toast(msg, "warn");
}

async function connect() {
  if (!hidSupported()) { toast("This browser can't reach USB devices. Use Chrome, Edge or Opera on a computer.", "error"); return; }
  try {
    const k = await Keyboard.request({ log });
    if (k) attach(k);
  } catch (e) {
    toast(e.name === "NotAllowedError" || e.name === "SecurityError" || e.name === "InvalidStateError"
      ? "Couldn't open the keyboard. On Linux, add the udev rule once (see About in Settings)."
      : e.message, "error");
  }
}
$("#connect").addEventListener("click", connect);
$("#welcome-connect").addEventListener("click", connect);
$("#demo").addEventListener("click", () => attach(new Keyboard(null, { simulated: true, log })));

async function pollStatus() {
  if (!kb || music.running) return;
  try {
    const s = await kb.status();
    if (!s) return;
    const bat = $("#battery");
    if (s.online && s.battery != null) {
      bat.classList.remove("hidden");
      bat.classList.toggle("low", s.battery <= 20);
      bat.classList.toggle("mid", s.battery > 20 && s.battery <= 50);
      bat.classList.toggle("charging", s.onCharger);
      bat.querySelector(".fill").setAttribute("width", (18.3 * s.battery / 100).toFixed(1));
      bat.querySelector("b").textContent = `${s.battery}%`;
      bat.title = s.powerName;
    } else {
      bat.classList.add("hidden");
      setSync("Keyboard asleep or off");
    }
  } catch {
    if (!kb.simulated) detach("Lost the keyboard.");
  }
}
setInterval(pollStatus, 5000);

if (hidSupported()) {
  navigator.hid.addEventListener("disconnect", (e) => { if (kb?.device === e.device) detach("Keyboard disconnected."); });
  Keyboard.reconnect({ log }).then((k) => (k ? attach(k) : detach())).catch(() => detach());
  if (/Linux/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent))
    $("#welcome-note").textContent = "Linux: add the udev rule once so the browser may open the keyboard (see About in Settings).";
} else {
  detach();
  $("#welcome-note").innerHTML = "This browser can't talk to USB keyboards (no WebHID). Use <b>Chrome, Edge or Opera on a computer</b>. Phones, Safari and Firefox aren't supported yet, but the demo works anywhere.";
  $("#welcome-connect").disabled = true;
}

// lights on/off
function showLights() { $("#lights").setAttribute("aria-pressed", String(!st.lights_off)); }
function toggleLights() {
  const off = st.lights_off;
  run(off ? "Lights on" : "Lights off", () => (off ? control.lightsOn(kb, st) : control.lightsOff(kb, st)), { after: showLights });
}
$("#lights").addEventListener("click", toggleLights);

// -- tabs ----------------------------------------------------------------------
let activeTab = "light";
$(".bottom-nav").innerHTML = $(".nav").innerHTML;
function updateHint() {
  let hint = HINTS[activeTab];
  if (activeTab === "light") {
    if (REACTIVE.has(light.mode)) hint = "This effect reacts to typing. Click keys to try it.";
    else if (light.mode === DATA.customMode) hint = "Your per-key design. Edit it in Per-key.";
  }
  $("#stage-hint").textContent = hint;
  $(".stage").classList.toggle("interactive", ["rgb", "remap", "macro"].includes(activeTab)
    || (activeTab === "light" && REACTIVE.has(light.mode)));
}

function showTab(name) {
  activeTab = name;
  $$(".nav button, .bottom-nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  $("#page-title").textContent = TABS[name][0];
  $("#page-sub").textContent = TABS[name][1];
  updateHint();
  history.replaceState(null, "", name === "light" ? location.pathname : `#${name}`);
}
$$(".nav button, .bottom-nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// -- lighting --------------------------------------------------------------------
const light = { ...st.light };
if (![...DATA.modes].some((m) => m.mode === light.mode) || light.mode === 22 || light.mode === 23) light.mode = 7;
const minis = [];

DATA.modes.filter((m) => m.mode !== 22 && m.mode !== 23).forEach((m) => {
  const b = document.createElement("button");
  b.className = "effect";
  b.dataset.mode = m.mode;
  const label = m.mode === DATA.customMode ? "Your design" : m.name;
  b.innerHTML = `<canvas></canvas><span>${label}</span>${REACTIVE.has(m.mode) ? '<em class="tag">reacts to typing</em>' : ""}`;
  b.title = m.name;
  b.addEventListener("click", () => {
    if (m.mode === DATA.customMode && !Object.values(st.colors).some((c) => Math.max(...c) > 0)) {
      toast("No design yet. Paint one here, then press Upload design.", "warn");
      showTab("rgb");
      return;
    }
    light.mode = m.mode; syncLight(); applyLightSoon();
  });
  $("#gallery").append(b);
  b.querySelector("canvas").style.aspectRatio = ASPECT;
  minis.push([m.mode, b.querySelector("canvas")]);
});

DATA.palette.forEach((p, i) => {
  const b = document.createElement("button");
  b.className = "swatch" + (p.rgb ? "" : " rainbow");
  if (p.rgb) b.style.background = `rgb(${p.rgb})`;
  b.title = p.name;
  b.dataset.i = i;
  b.addEventListener("click", () => { light.color = i; syncLight(); applyLightSoon(); });
  $("#palette").append(b);
});

function syncLight() {
  const m = DATA.modes.find((x) => x.mode === light.mode);
  const A = DATA.attr;
  $$(".effect").forEach((b) => b.classList.toggle("active", Number(b.dataset.mode) === light.mode));
  $("#effect-name").textContent = m.name;
  for (const id of ["brightness", "speed"]) {
    const el = $("#" + id);
    el.value = light[id];
    el.disabled = !(m.attr & A[id]) && !(id === "brightness" && m.mode === DATA.customMode);
    rangeFill(el);
  }
  setSeg("direction", light.direction);
  $("#direction").setAttribute("aria-disabled", String(!(m.attr & A.direction)));
  $$("#palette .swatch").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === light.color));
  $("#palette").style.opacity = m.attr & A.color ? 1 : 0.3;
  $("#palette").style.pointerEvents = m.attr & A.color ? "" : "none";
  updateHint();
}

const applyLightSoon = debounce(() => {
  const l = { ...light };
  const name = DATA.modes.find((x) => x.mode === l.mode).name;
  run(name, () => (l.mode === DATA.customMode
    ? control.uploadCustom(kb, st, st.colors, l.brightness)
    : control.applyLight(kb, st, l)), { quiet: true, after: showLights });
}, 300);

for (const id of ["brightness", "speed"]) {
  $("#" + id).addEventListener("input", (e) => { light[id] = Number(e.target.value); rangeFill(e.target); applyLightSoon(); });
}
seg("direction", (v) => { light.direction = Number(v); applyLightSoon(); });

// -- per-key ---------------------------------------------------------------------
let brush = [255, 61, 119], brush2 = [61, 123, 255], tool = "paint", liveRgb = false;
const undoStack = [], redoStack = [];
const paint = Object.fromEntries(DATA.keys.map((k) => [k.led, st.colors[k.led] ?? [0, 0, 0]]));
const selected = new Set();

function paintKey(key, drag) {
  if (tool === "paint") {
    if (!drag) snapshot();
    paint[key.led] = brush.slice();
    dirty();
  } else if (tool === "pick" && !drag) {
    setBrush(paint[key.led]);
    setTool("paint");
  } else if (tool === "select") {
    if (drag) selected.add(key.led);
    else selected.has(key.led) ? selected.delete(key.led) : selected.add(key.led);
    $("#sel-count").textContent = selected.size ? `${selected.size} selected` : "";
  }
}

function dirty(on = true) {
  $("#dirty").textContent = on ? "Not uploaded yet" : "";
  if (on && liveRgb && kb) kb.sendFrame(paint).catch(() => {});
}
function snapshot() {
  undoStack.push(JSON.stringify(paint));
  if (undoStack.length > 60) undoStack.shift();
  redoStack.length = 0;
}
function restorePaint(json) { Object.assign(paint, JSON.parse(json)); dirty(); }
const undo = () => { if (undoStack.length) { redoStack.push(JSON.stringify(paint)); restorePaint(undoStack.pop()); } };
const redo = () => { if (redoStack.length) { undoStack.push(JSON.stringify(paint)); restorePaint(redoStack.pop()); } };
$("#undo").addEventListener("click", undo);
$("#redo").addEventListener("click", redo);

function setBrush(rgb) { brush = rgb.slice(); $("#brush").value = hex(brush); }
function setTool(t) { tool = t; setSeg("tool", t); }
seg("tool", (t) => { tool = t; });

Object.entries(NAMED).forEach(([name, rgb]) => {
  const b = document.createElement("button");
  b.className = "swatch";
  b.style.background = `rgb(${rgb})`;
  b.title = name;
  b.addEventListener("click", () => { setBrush(rgb); if (tool === "pick") setTool("paint"); });
  $("#swatches").append(b);
});
$("#brush").addEventListener("input", (e) => (brush = fromHex(e.target.value)));
$("#brush2").addEventListener("input", (e) => (brush2 = fromHex(e.target.value)));

for (const key of Object.keys(DATA.patterns)) {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = PATTERN_LABELS[key] ?? key;
  b.title = DATA.patterns[key];
  b.addEventListener("click", () => {
    snapshot();
    const colors = pattern(key, brush, brush2);
    for (const led of selected.size ? selected : Object.keys(paint)) paint[led] = colors[led];
    dirty();
  });
  $("#patterns").append(b);
}
$("#clear-sel").addEventListener("click", () => { selected.clear(); $("#sel-count").textContent = ""; });

// presets
const PRESETS = "openajazz-presets";
const presets = () => {
  try { return JSON.parse(localStorage.getItem(PRESETS) || localStorage.getItem("ak820ctl-presets") || "{}"); }
  catch { return {}; }
};
function savePresets(all) { try { localStorage.setItem(PRESETS, JSON.stringify(all)); } catch { /* storage blocked */ } }
function refreshPresets() {
  const box = $("#preset-list");
  box.innerHTML = "";
  const all = presets();
  if (!Object.keys(all).length) box.innerHTML = '<span class="muted small">No saved designs yet.</span>';
  for (const [name, colors] of Object.entries(all).sort()) {
    const vals = Object.values(colors).filter((c) => Math.max(...c) > 20);
    const pick = (i) => (vals.length ? `rgb(${vals[Math.floor(i * (vals.length - 1))]})` : "#222");
    const chip = document.createElement("span");
    chip.className = "preset";
    chip.innerHTML = `<i style="background:linear-gradient(135deg, ${pick(0)}, ${pick(0.5)}, ${pick(1)})"></i><span></span><button title="Delete">${icon("x", "xs")}</button>`;
    chip.querySelector("span").textContent = name;
    chip.addEventListener("click", (e) => {
      if (e.target.closest("button")) {
        const a = presets(); delete a[name]; savePresets(a); refreshPresets(); return;
      }
      snapshot(); Object.assign(paint, colors); dirty();
      toast(`Loaded '${name}'. Upload to store it.`);
    });
    box.append(chip);
  }
}
$("#save-preset").addEventListener("click", () => {
  const name = $("#preset-name").value.trim();
  if (!name) { toast("Give the design a name.", "warn"); return; }
  const all = presets(); all[name] = { ...paint }; savePresets(all);
  $("#preset-name").value = "";
  refreshPresets();
  toast(`Saved '${name}'`);
});
$("#export").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(paint, null, 1)], { type: "application/json" }));
  a.download = "openajazz-design.json";
  a.click();
});
$("#import").addEventListener("change", async (e) => {
  try {
    const data = JSON.parse(await e.target.files[0].text());
    snapshot();
    for (const [led, rgb] of Object.entries(data)) if (led in paint) paint[led] = rgb;
    dirty();
  } catch { toast("That file isn't an AK820 design export.", "error"); }
});

$("#live-rgb").addEventListener("change", (e) => {
  if (!kb) { e.target.checked = false; toast("Connect first.", "warn"); return; }
  liveRgb = e.target.checked;
  if (liveRgb) run("Live preview on", async () => { await kb.enterLiveMode(); await kb.sendFrame(paint); });
  else run("Live preview off", () => (st.lights_off ? kb.lightsOff() : control.lightsOn(kb, st)));
});
$("#upload").addEventListener("click", () => {
  const colors = { ...paint };
  run("Design stored on the keyboard", () => control.uploadCustom(kb, st, colors, light.brightness), {
    after: () => {
      dirty(false); showLights();
      light.mode = DATA.customMode; syncLight();
      liveRgb = false; $("#live-rgb").checked = false;
    },
  });
});

// -- remap ----------------------------------------------------------------------
let remapSrc = null;
for (const n of Object.keys(DATA.hid)) $("#targets").append(new Option(`key: ${n}`, n));
for (const n of Object.keys(DATA.media)) $("#targets").append(new Option(`media: ${n}`, n));
for (const n of QUICK_TARGETS) {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = n;
  b.addEventListener("click", () => { $("#remap-target").value = n; });
  $("#quick-targets").append(b);
}
const remapLayer = () => ($("#remap-fn").checked ? 1 : 0);
function remapMarks() {
  const marks = {};
  for (const key of Object.keys(st.remaps)) {
    const [lay, src] = key.split(":").map(Number);
    if (lay === remapLayer()) marks[LED_OF[src]] = "remapped";
  }
  if (remapSrc != null) marks[LED_OF[remapSrc]] = "picked";
  return marks;
}

function row(capText, what, btnText, onClick, whatIcon = null) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="keycap small"></span><span class="what">${whatIcon ? icon(whatIcon, "xs") : ""}<span></span></span><button class="btn ghost sm"></button>`;
  li.children[0].textContent = capText;
  li.children[1].lastChild.textContent = what;
  li.children[2].textContent = btnText;
  li.children[2].addEventListener("click", onClick);
  return li;
}

function refreshRemaps() {
  const ul = $("#remap-list");
  ul.innerHTML = "";
  const entries = Object.entries(st.remaps);
  if (!entries.length) ul.innerHTML = '<li class="empty">Nothing remapped. Click a key on the board.</li>';
  for (const [key, dst] of entries) {
    const [lay, src] = key.split(":").map(Number);
    ul.append(row(keyLabel(lay, src), codeName(dst), "Restore",
      () => run(`${keyLabel(lay, src)} restored`, () => control.restoreKey(kb, st, lay, src), { after: keysChanged }),
      "arrow-right"));
  }
  const cap = $("#remap-cap");
  cap.textContent = remapSrc == null ? "?" : keyLabel(remapLayer(), remapSrc);
  cap.classList.toggle("empty", remapSrc == null);
  const cur = remapSrc == null ? null : st.remaps[`${remapLayer()}:${remapSrc}`];
  $("#remap-target").placeholder = cur != null ? `currently ${codeName(cur)}` : "search a function: esc, volup, f13…";
}
$("#remap-fn").addEventListener("change", refreshRemaps);
$("#apply-remap").addEventListener("click", () => {
  if (remapSrc == null) { toast("Click a key on the board first.", "warn"); return; }
  let dst;
  try { dst = targetCode($("#remap-target").value); } catch (e) { toast(e.message, "error"); return; }
  const layer = remapLayer(), src = remapSrc;
  run(`${keyLabel(layer, src)} now does ${codeName(dst)}`, () => control.remap(kb, st, layer, src, dst), { after: keysChanged });
});
$("#restore-key").addEventListener("click", () => {
  if (remapSrc == null) { toast("Click a key on the board first.", "warn"); return; }
  const layer = remapLayer(), src = remapSrc;
  run(`${keyLabel(layer, src)} restored`, () => control.restoreKey(kb, st, layer, src), { after: keysChanged });
});
$("#reset-keys").addEventListener("click", async () => {
  if (await confirmDialog("Reset all keys?", "Every remap and macro goes back to the default."))
    run("All keys reset", () => control.resetKeys(kb, st), { after: keysChanged });
});
function keysChanged() { refreshRemaps(); refreshMacros(); }

// -- macro ----------------------------------------------------------------------
let macroSrc = null;
const macroLayer = () => ($("#macro-fn").checked ? 1 : 0);
function macroMarks() {
  const marks = {};
  for (const key of Object.keys(st.macros)) {
    const [lay, src] = key.split(":").map(Number);
    if (lay === macroLayer()) marks[LED_OF[src]] = "macro";
  }
  if (macroSrc != null) marks[LED_OF[macroSrc]] = "picked";
  return marks;
}
function pickMacroKey(code) {
  macroSrc = code;
  const m = st.macros[`${macroLayer()}:${code}`];
  if (m) { $("#macro-events").value = m.events; $("#macro-repeat").value = m.repeat; previewMacro(); }
  refreshMacros();
}
function refreshMacros() {
  const ul = $("#macro-list");
  ul.innerHTML = "";
  const entries = Object.entries(st.macros);
  if (!entries.length) ul.innerHTML = '<li class="empty">No macros yet.</li>';
  for (const [key, m] of entries) {
    const [lay, src] = key.split(":").map(Number);
    ul.append(row(keyLabel(lay, src), `${m.events}${m.repeat > 1 ? `  (repeat ${m.repeat})` : ""}`, "Remove",
      () => run(`Macro on ${keyLabel(lay, src)} removed`, () => control.restoreKey(kb, st, lay, src), { after: keysChanged })));
  }
  const cap = $("#macro-cap");
  cap.textContent = macroSrc == null ? "?" : keyLabel(macroLayer(), macroSrc);
  cap.classList.toggle("empty", macroSrc == null);
}
$("#macro-fn").addEventListener("change", refreshMacros);

function previewMacro() {
  const box = $("#macro-preview");
  box.innerHTML = "";
  try {
    for (const [code, release, delay] of parseMacroEvents($("#macro-events").value)) {
      const ev = document.createElement("span");
      ev.className = "ev " + (release ? "up" : "down");
      ev.innerHTML = icon(release ? "arrow-up" : "arrow-down", "xs") + "<span></span>";
      ev.lastChild.textContent = codeName(code);
      const gap = document.createElement("span");
      gap.className = "ev-gap";
      gap.textContent = `${delay}ms`;
      box.append(ev, gap);
    }
  } catch (e) {
    box.innerHTML = `<span class="muted small"></span>`;
    box.firstChild.textContent = e.message;
  }
}
$("#macro-events").addEventListener("input", previewMacro);
$("#apply-macro").addEventListener("click", () => {
  const raw = $("#macro-events").value.trim();
  const repeat = Math.min(255, Math.max(1, Number($("#macro-repeat").value) || 1));
  let events;
  try { events = parseMacroEvents(raw); } catch (e) { toast(e.message, "error"); return; }
  if (macroSrc == null) { toast("Click a key on the board first.", "warn"); return; }
  if (!events.length) { toast("Type at least one key, e.g. +lctrl c -lctrl", "warn"); return; }
  const layer = macroLayer(), src = macroSrc;
  run(`Macro on ${keyLabel(layer, src)} stored`, () => control.setMacro(kb, st, layer, src, raw, repeat), { after: keysChanged });
});

// -- music -----------------------------------------------------------------------
const music = { running: false };
// Worker-driven clock: main-thread timers stall when the tab is in the background.
const ticker = new Worker("ticker.js");
ticker.onmessage = () => music.running && musicTick();
seg("music-source", () => {});
seg("music-style", (v) => { if (music.renderer) music.renderer.style = v; });
let musicRgb = [255, 92, 138];
const musicColour = () => (segValue("music-color") === "single" ? musicRgb : null);
function setMusicRgb(rgb) {
  musicRgb = rgb.slice();
  $("#music-hex").value = hex(musicRgb);
  if (music.renderer) music.renderer.color = musicColour();
}
seg("music-color", () => {
  $("#music-pick").classList.toggle("hidden", segValue("music-color") !== "single");
  if (music.renderer) music.renderer.color = musicColour();
});
$("#music-hex").addEventListener("input", (e) => setMusicRgb(fromHex(e.target.value)));
for (const [name, rgb] of Object.entries(NAMED)) {
  if (name === "off") continue;
  const b = document.createElement("button");
  b.className = "swatch";
  b.style.background = `rgb(${rgb})`;
  b.title = name;
  b.addEventListener("click", () => setMusicRgb(rgb));
  $("#music-swatches").append(b);
}
$("#music-sens").addEventListener("input", (e) => {
  rangeFill(e.target);
  if (music.analyzer) music.analyzer.sensitivity = 0.4 + 0.3 * Number(e.target.value);
});
rangeFill($("#music-sens"));

async function startMusic() {
  try {
    const stream = segValue("music-source") === "mic"
      ? await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } })
      : await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((t) => t.stop());
      toast("That share has no audio. Turn on 'Share tab audio' in the picker.", "error");
      return;
    }
    music.stream = stream;
    music.ctx = new AudioContext();
    const node = music.ctx.createAnalyser();
    node.fftSize = 2048;
    node.smoothingTimeConstant = 0;
    music.ctx.createMediaStreamSource(stream).connect(node);
    music.analyzer = new Analyzer(node, music.ctx.sampleRate, 0.4 + 0.3 * Number($("#music-sens").value));
    music.renderer = new MusicRenderer(segValue("music-style"), musicColour());
    if (kb) await kb.enterLiveMode();
    music.running = true;
    stream.getTracks().forEach((t) => t.addEventListener("ended", stopMusic));
    ticker.postMessage(1000 / 30);
    $("#music-toggle span").textContent = "Stop";
    $("#music-toggle").classList.replace("primary", "danger");
    $("#music-state").textContent = kb ? "running" : "running, screen only";
  } catch (e) {
    if (e.name !== "NotAllowedError") toast(`Music sync: ${e.message}`, "error");
  }
}

let sending = false;
function musicTick() {
  music.analyzer.update();
  musicFrame = music.renderer.frame(music.analyzer.levels, music.analyzer.bass);
  if (activeTab === "music") drawBars(music.analyzer.levels);
  if (kb && !sending) {
    sending = true;
    kb.sendFrame(musicFrame).catch(() => {}).finally(() => (sending = false));
  }
}

function drawBars(levels) {
  const c = $("#music-bars"), ctx = c.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr;
  const w = c.width / BANDS;
  levels.forEach((lv, i) => {
    const h = Math.max(2 * dpr, c.height * lv);
    const g = ctx.createLinearGradient(0, c.height, 0, c.height - h);
    g.addColorStop(0, "#ff7a3d"); g.addColorStop(0.5, "#ff3d77"); g.addColorStop(1, "#8b5cf6");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.roundRect(i * w + w * 0.18, c.height - h, w * 0.64, h, 4 * dpr);
    ctx.fill();
  });
}

function stopMusic() {
  if (!music.running) return;
  music.running = false;
  ticker.postMessage(0);
  music.stream?.getTracks().forEach((t) => t.stop());
  music.ctx?.close();
  musicFrame = {};
  $("#music-toggle span").textContent = "Start";
  $("#music-toggle").classList.replace("danger", "primary");
  $("#music-state").textContent = "stopped";
  drawBars(new Array(BANDS).fill(0));
  if (kb && !st.lights_off) run("Lighting restored", () => control.lightsOn(kb, st), { quiet: true });
}
$("#music-toggle").addEventListener("click", () => (music.running ? stopMusic() : startMusic()));

// Closing the page stops the stream and the keyboard falls back to its stored
// lighting, so ask first while something is streaming.
window.addEventListener("beforeunload", (e) => {
  if (music.running || liveRgb) e.preventDefault();
});

// -- settings --------------------------------------------------------------------
setSeg("sleep", st.settings.sleep);
setSeg("response", st.settings.response);
$("#fn-switch").checked = st.settings.fn;
function applySettings() {
  const s = Number(segValue("sleep")), r = Number(segValue("response")), fn = $("#fn-switch").checked;
  run("Settings saved", () => control.applySettings(kb, st, s, r, fn), { quiet: true });
}
seg("sleep", applySettings);
seg("response", applySettings);
$("#fn-switch").addEventListener("change", applySettings);
$("#factory").addEventListener("click", async () => {
  if (await confirmDialog("Restore factory settings?", "Everything stored on the keyboard gets erased."))
    run("Factory reset done", () => control.factoryReset(kb, st), { after: () => { keysChanged(); showLights(); } });
});
$("#udev-cmd").addEventListener("click", (e) => {
  navigator.clipboard?.writeText(e.target.textContent).then(() => toast("Copied"));
});
$("#version").textContent = `v${DATA.version}`;

// -- shortcuts & render loop -----------------------------------------------------
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea")) return;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
  else if (mod && (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))) { e.preventDefault(); redo(); }
  else if (!mod && e.key === "o") toggleLights();
  else if (!mod && /^[1-6]$/.test(e.key)) showTab(Object.keys(TABS)[e.key - 1]);
});

const t0 = performance.now();
let frameNo = 0, lastDraw = 0;
function loop(now) {
  requestAnimationFrame(loop);
  if (now - lastDraw < 1000 / 30) return;  // 30 fps is plenty
  lastDraw = now;
  const t = (now - t0) / 1000;
  const [colors, opts] = stageContent(t);
  drawBoard(stage, colors, opts);
  if (activeTab === "light" && frameNo++ % 2 === 0) {
    for (const [mode, canvas] of minis) {
      const f = previewFrame({ ...light, mode }, t, st.colors);
      drawBoard(canvas, f, { labels: 0, glow: false });
    }
  }
}

syncLight();
refreshPresets();
keysChanged();
showLights();
showTab($(`#tab-${location.hash.slice(1)}`) ? location.hash.slice(1) : "light");
drawBars(new Array(BANDS).fill(0));
requestAnimationFrame(loop);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
