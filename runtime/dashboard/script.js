// =====================================================================
// car-calib dashboard frontend
// Sections (search by banner):
//   §1  Config + DOM refs
//   §2  Script step editor (form, render, edit/remove)
//   §3  Run/Stop + relay light
//   §4  Status renderers (header pills, status bar, metric grid)
//   §5  Event log
//   §6  Tabs (zone-2 + modal)
//   §7  Progress + step elapsed
//   §8  Polling (status / route_script status)
//   §9  Routes table (filter / sort / search / bulk)
//  §10  Route summary modal
//  §11  Presets CRUD
//  §12  Tune panel (steering controller params)
// =====================================================================
(function () {
"use strict";

// §1 ─────────────────────────────────────────────────────────────────
const TOKEN = window.DASHBOARD_CONFIG.TOKEN;
const STREAM = window.DASHBOARD_CONFIG.STREAM;
const STATUS = window.DASHBOARD_CONFIG.STATUS;
const qp = TOKEN ? ("?token=" + encodeURIComponent(TOKEN)) : "";
document.getElementById("stream").src = STREAM + qp;

const steps = [];
const tbody = document.querySelector("#steps tbody");
const preview = document.getElementById("preview");
const runPill = document.getElementById("runPill");
const runDetail = document.getElementById("runDetail");
const progressBar = document.getElementById("progressBar");
const progressSegments = document.getElementById("progressSegments");
const actionInput = document.getElementById("action");
const durationInput = document.getElementById("duration");
const addBtn = document.getElementById("add");
const cancelEditBtn = document.getElementById("cancelEdit");
const manualJoystick = document.getElementById("manualJoystick");
const manualJoystickKnob = document.getElementById("manualJoystickKnob");
const manualPill = document.getElementById("manualPill");
const manualDetail = document.getElementById("manualDetail");
let currentRunningStep = 0;
let isRunning = false;
let editingIndex = -1;
let manualActive = false;
let manualPointerId = null;
let manualDrive = 0;
let manualSteer = 0;
let manualHeartbeat = null;

function resetEditor() {
  editingIndex = -1;
  addBtn.textContent = "+ add step";
  cancelEditBtn.style.display = "none";
}

function loadEditorFromStep(idx) {
  const step = steps[idx];
  if (!step) return;
  editingIndex = idx;
  actionInput.value = step.action;
  durationInput.value = String(step.duration_s);
  addBtn.textContent = "save edit";
  cancelEditBtn.style.display = "";
}

function render() {
  tbody.innerHTML = "";
  steps.forEach((s, i) => {
    const tr = document.createElement("tr");
    tr.className = "step-row";
    tr.dataset.idx = i;
    if (isRunning) {
      if (i + 1 < currentRunningStep) tr.classList.add("done");
      else if (i + 1 === currentRunningStep) tr.classList.add("active");
      else tr.classList.add("pending");
    }
    tr.innerHTML = `<td class="idx">${i+1}</td><td>${safeText(s.action)}</td><td>${s.duration_s.toFixed(1)} s</td><td>${isRunning ? "" : `<button data-i="${i}" class="play" title="run only this step">▶</button> <button data-i="${i}" class="edit">edit</button> <button data-i="${i}" class="rm">×</button>`}</td>`;
    tbody.appendChild(tr);
  });
  preview.textContent = JSON.stringify({steps}, null, 2);
}

addBtn.onclick = () => {
  if (isRunning) return;
  const action = actionInput.value;
  const duration_s = parseFloat(durationInput.value || "0");
  if (!isFinite(duration_s) || duration_s <= 0) { runDetail.textContent = "duration phải > 0"; return; }
  if (editingIndex >= 0 && editingIndex < steps.length) {
    steps[editingIndex] = {action, duration_s};
    resetEditor();
  } else {
    steps.push({action, duration_s});
  }
  render();
};

cancelEditBtn.onclick = () => {
  if (isRunning) return;
  resetEditor();
};
document.getElementById("clear").onclick = () => {
  if (isRunning) return;
  steps.length = 0;
  resetEditor();
  render();
};
tbody.onclick = (e) => {
  if (isRunning) return;
  const t = e.target;
  const idx = parseInt(t.dataset.i, 10);
  if (!Number.isFinite(idx)) return;

  if (t.classList.contains("play")) {
    playSingleStep(idx);
    return;
  }

  if (t.classList.contains("edit")) {
    loadEditorFromStep(idx);
    return;
  }

  if (t.classList.contains("rm")) {
    steps.splice(idx, 1);
    if (editingIndex === idx) resetEditor();
    else if (editingIndex > idx) editingIndex -= 1;
    render();
  }
};

async function playSingleStep(idx) {
  const step = steps[idx];
  if (!step) return;
  if (editingIndex >= 0) { runDetail.textContent = "save edit first"; return; }
  runDetail.textContent = `step ${idx + 1}: starting…`;
  try {
    const r = await fetch("/route/script/step" + qp, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: step.action, duration_s: step.duration_s}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      runDetail.textContent = "step error: " + (j.detail || r.status);
      return;
    }
    runDetail.textContent = "";
  } catch (e) {
    runDetail.textContent = "step network error";
  }
}

document.getElementById("run").onclick = async () => {
  if (editingIndex >= 0) { runDetail.textContent = "save edit first"; return; }
  if (steps.length === 0) { runDetail.textContent = "add steps first"; return; }
  runDetail.textContent = "submitting…";
  const presetSel = document.getElementById("presetSelect");
  const presetInput = document.getElementById("presetName");
  const preset_name = (presetSel && presetSel.value) || (presetInput && presetInput.value.trim()) || null;
  const body = {steps, preset_name, description: null};
  const r = await fetch("/route/script" + qp, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { runDetail.textContent = "error: " + JSON.stringify(j.detail || r.status); return; }
  runDetail.textContent = "";
};
document.getElementById("stop").onclick = async () => {
  await fetch("/route/script/stop" + qp, {method: "POST"});
  runDetail.textContent = "stopping…";
};

async function setLight(on) {
  const sep = qp ? "&" : "?";
  const toggle = document.getElementById("lightToggle");
  const label = document.getElementById("lightLabel");
  try {
    const r = await fetch("/route/relay" + qp + sep + "on=" + (on ? 1 : 0), {method: "POST"});
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      label.textContent = "light error: " + (j.detail || j.error || r.status);
      toggle.checked = !on;  // revert switch on failure
      return;
    }
    label.textContent = on ? "light on" : "light off";
  } catch (e) {
    label.textContent = "light error: network";
    toggle.checked = !on;
  }
}
document.getElementById("lightToggle").onchange = (e) => setLight(e.target.checked);

let _powerBusy = false;
async function sendPower(on) {
  const sep = qp ? "&" : "?";
  const label = document.getElementById("powerLabel");
  const onBtn = document.getElementById("powerOnBtn");
  const offBtn = document.getElementById("powerOffBtn");
  if (_powerBusy) return;  // debounce: a pulse request is already in flight
  if (!on && !confirm("Tắt xe? Relay sẽ chập giữ 3s.")) { label.textContent = ""; return; }
  _powerBusy = true;
  onBtn.disabled = true; offBtn.disabled = true;
  label.textContent = on ? "đang bật…" : "đang tắt…";
  try {
    const r = await fetch("/control/power" + qp + sep + "on=" + (on ? 1 : 0), {method: "POST"});
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      label.textContent = "power error: " + (j.detail || j.error || r.status);
      return;
    }
    label.textContent = on ? "đã gửi BẬT" : "đã gửi TẮT";
    setTimeout(() => { label.textContent = ""; }, 2000);
  } catch (e) {
    label.textContent = "power error: network";
  } finally {
    // Hold the lock long enough to cover the longest (OFF) pulse so a rapid
    // second click cannot overlap the relay pulse on the actuator side.
    setTimeout(() => {
      _powerBusy = false;
      updateActuatorControls({stale: false, online: true, estop: false});
    }, 3500);
  }
}
document.getElementById("powerOnBtn").onclick = () => sendPower(true);
document.getElementById("powerOffBtn").onclick = () => sendPower(false);

// §3b ── Camera select combo box ────────────────────────────────────────
const cameraSelect = document.getElementById("cameraSelect");
const cameraLabel = document.getElementById("cameraLabel");
let cameraBusy = false;

async function refreshCameraList() {
  try {
    const r = await fetch("/api/camera/list" + qp);
    if (!r.ok) {
      cameraLabel.textContent = "camera scan error: " + r.status;
      return;
    }
    const data = await r.json();
    const cameras = data.cameras || [];
    cameraSelect.innerHTML = "";
    if (cameras.length === 0) {
      cameraSelect.appendChild(new Option("no camera found", ""));
      cameraLabel.textContent = "";
      return;
    }
    for (const idx of cameras) {
      cameraSelect.appendChild(new Option("camera " + idx + " (/dev/video" + idx + ")", idx));
    }
    cameraSelect.value = String(data.current);
    cameraLabel.textContent = "active: /dev/video" + data.current;
  } catch (e) {
    cameraLabel.textContent = "camera scan error: network";
  }
}

async function selectCamera(index) {
  if (cameraBusy) return;
  cameraBusy = true;
  cameraLabel.textContent = "switching…";
  try {
    const r = await fetch("/api/camera/select" + qp, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({index: Number(index)}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      cameraLabel.textContent = "camera error: " + (j.detail || r.status);
      return;
    }
    cameraLabel.textContent = "switching to /dev/video" + index + "…";
  } catch (e) {
    cameraLabel.textContent = "camera error: network";
  } finally {
    cameraBusy = false;
  }
}

cameraSelect.onchange = (e) => selectCamera(e.target.value);
document.getElementById("cameraRefresh").onclick = refreshCameraList;
refreshCameraList();

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function setManualUi(cls, text, detail) {
  if (manualPill) {
    manualPill.className = "pill " + cls;
    manualPill.textContent = text;
  }
  if (manualDetail) manualDetail.textContent = detail || "";
}

function updateJoystickKnob(drive, steer) {
  if (!manualJoystickKnob || !manualJoystick) return;
  const rect = manualJoystick.getBoundingClientRect();
  const radius = Math.max(1, rect.width / 2 - 24);
  const x = steer * radius;
  const y = -drive * radius;
  manualJoystickKnob.style.transform = `translate(calc(-50% + ${x.toFixed(1)}px), calc(-50% + ${y.toFixed(1)}px))`;
}

function joystickAxesFromEvent(e) {
  const rect = manualJoystick.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const radius = Math.max(1, rect.width / 2 - 24);
  return {
    drive: clamp(-(e.clientY - cy) / radius, -1, 1),
    steer: clamp((e.clientX - cx) / radius, -1, 1),
  };
}

async function sendManualOverride(active) {
  const body = {
    active: !!active,
    drive: active ? manualDrive : 0,
    steer: active ? manualSteer : 0,
    ts: Date.now() / 1000,
  };
  try {
    const r = await fetch("/api/manual_override" + qp, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      setManualUi("pill-error", "manual error", j.detail || String(r.status));
      return;
    }
    if (active) {
      setManualUi("pill-running", "manual active", `drive ${manualDrive.toFixed(2)} · steer ${manualSteer.toFixed(2)}`);
    }
  } catch (e) {
    setManualUi("pill-error", "manual network", "release joystick");
  }
}

function startManual(e) {
  if (!manualJoystick || manualActive) return;
  manualActive = true;
  manualPointerId = e.pointerId;
  manualJoystick.setPointerCapture(e.pointerId);
  manualJoystick.classList.add("active");
  const axes = joystickAxesFromEvent(e);
  manualDrive = axes.drive;
  manualSteer = axes.steer;
  updateJoystickKnob(manualDrive, manualSteer);
  sendManualOverride(true);
  manualHeartbeat = setInterval(() => sendManualOverride(true), 100);
}

function moveManual(e) {
  if (!manualActive || e.pointerId !== manualPointerId) return;
  const axes = joystickAxesFromEvent(e);
  manualDrive = axes.drive;
  manualSteer = axes.steer;
  updateJoystickKnob(manualDrive, manualSteer);
}

function stopManual() {
  if (!manualActive) return;
  const pointerId = manualPointerId;
  manualActive = false;
  manualPointerId = null;
  manualDrive = 0;
  manualSteer = 0;
  if (manualHeartbeat) clearInterval(manualHeartbeat);
  manualHeartbeat = null;
  if (manualJoystick) {
    if (pointerId !== null && manualJoystick.hasPointerCapture && manualJoystick.hasPointerCapture(pointerId)) {
      manualJoystick.releasePointerCapture(pointerId);
    }
    manualJoystick.classList.remove("active");
  }
  updateJoystickKnob(0, 0);
  setManualUi("pill-idle", "idle", "released");
  sendManualOverride(false);
}

function renderManualStatus(tel) {
  if (manualActive) return;
  const t = tel || {};
  const reason = t.manual_blocked_reason || "";
  if (reason === "timeout") {
    setManualUi("pill-error", "manual timeout", "base stopped");
  } else if (reason) {
    setManualUi("pill-paused", "blocked", reason);
  } else if (t.manual_override_active) {
    setManualUi("pill-running", "manual active", `drive ${Number(t.manual_drive || 0).toFixed(2)} · steer ${Number(t.manual_steer || 0).toFixed(2)}`);
  } else {
    setManualUi("pill-idle", "idle", "");
  }
}

if (manualJoystick) {
  manualJoystick.addEventListener("pointerdown", startManual);
  manualJoystick.addEventListener("pointermove", moveManual);
  manualJoystick.addEventListener("pointerup", stopManual);
  manualJoystick.addEventListener("pointercancel", stopManual);
  window.addEventListener("blur", stopManual);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopManual(); });
  updateJoystickKnob(0, 0);
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.onclick = () => {
    const target = btn.dataset.tab;
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === target));
    document.querySelectorAll(".tab-pane").forEach(p => p.classList.toggle("active", p.dataset.pane === target));
  };
});

function renderProgressSegments(total, currentIdx, fillRatio) {
  if (!progressSegments) return;
  if (!total) { progressSegments.innerHTML = ""; progressSegments.style.gridTemplateColumns = ""; return; }
  progressSegments.style.gridTemplateColumns = `repeat(${total}, 1fr)`;
  let html = "";
  for (let i = 1; i <= total; i++) {
    if (i < currentIdx) html += '<div class="seg done"></div>';
    else if (i === currentIdx) html += `<div class="seg active" style="--seg-fill:${Math.round((fillRatio||0)*100)}%"></div>`;
    else html += '<div class="seg"></div>';
  }
  progressSegments.innerHTML = html;
}

function setPill(klass, text) {
  runPill.className = "pill " + klass;
  runPill.textContent = text;
}

function safeText(v) {
  if (v === null || v === undefined || v === "") return "-";
  return String(v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

function fmtAge(s) {
  if (s == null || !Number.isFinite(Number(s))) return "-";
  const v = Number(s);
  if (v < 1) return Math.round(v * 1000) + " ms";
  if (v < 60) return v.toFixed(1) + " s";
  return Math.floor(v / 60) + "m " + Math.floor(v % 60) + "s";
}

function fmtUptime(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "-";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

function detectMode(payload) {
  const src = payload && payload.source;
  if (src === "esp32" || src === "esp8266") {
    return {kind: "esp", label: `${src.toUpperCase()} · MQTT`};
  }
  return {kind: "mqtt", label: "MQTT actuator"};
}

function renderStatusBar(rpi, actuator) {
  const payload = (rpi && rpi.payload) || {};
  const mode = detectMode(payload);
  const isEsp = mode.kind === "esp";
  const stale = !rpi || !!rpi.stale;
  const online = !!(rpi && rpi.online);
  const estop = !!payload.estop_active;
  const estopCls = estop ? "text-bad" : "text-ok";
  const estopTxt = estop ? "ACTIVE" : "CLEAR";
  const rpiCls = online ? "text-ok" : "text-bad";
  const rpiTxt = online ? "ONLINE" : "OFFLINE";
  const rpiSub = fmtAge(rpi && rpi.age_s);
  const mqttConnected = payload.mqtt_connected !== false && !stale;
  const mqttTxt = stale ? "stale" : (mqttConnected ? "connected" : "disconnected");
  const mqttCls = mqttConnected ? "text-ok" : "text-bad";
  const pigpio = payload.pigpio_connected;
  const pigpioTxt = stale ? "unknown" : (pigpio ? "ok" : "error");
  const pigpioCls = !stale && pigpio ? "text-ok" : "text-bad";

  // sticky header pills (always visible)
  setHeaderPill("modePill", isEsp ? "warn" : "ok", "Mode", mode.label);
  setHeaderPill("hPillEstop", estop ? "bad" : "ok", "E-stop", estopTxt);
  setHeaderPill("hPillRpi", online ? "ok" : "bad", "Actuator", rpiTxt, rpiSub);
  setHeaderPill("hPillMqtt", mqttConnected ? "ok" : "bad", "MQTT", mqttTxt);

  // Power controls publish to backend; OFF must remain available even if telemetry flaps.
  updateActuatorControls({stale, online, estop});

  const ctrlLabel = isEsp ? "ESP controller" : "Actuator";
  const fourthCell = `<div class="status-cell"><div class="label">GPIO</div><div class="value ${pigpioCls}">${pigpioTxt}</div></div>`;

  document.getElementById("statusBar").innerHTML = `
    <div class="status-grid">
      <div class="status-cell"><div class="label">E-stop</div><div class="value ${estopCls}">${estopTxt}</div></div>
      <div class="status-cell"><div class="label">${ctrlLabel}</div><div class="value ${rpiCls}">${rpiTxt}</div><div class="sub">${safeText(rpiSub)}</div></div>
      <div class="status-cell"><div class="label">MQTT</div><div class="value ${mqttCls}">${mqttTxt}</div></div>
      ${fourthCell}
    </div>
    <div style="margin-top:4px;font-size:12px;color:#666;text-align:center">
      ${fmtUptime(payload.uptime_s ?? payload.uptime_sec)} · ${safeText(payload.hostname || (isEsp ? "ESP serial bridge" : ""))}
    </div>`;
}

function setHeaderPill(id, cls, label, value, sub) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = "h-pill " + (cls || "");
  const lbl = el.querySelector(".lbl");
  if (lbl) lbl.textContent = `${label} ${value}`;
  const subEl = el.querySelector(".sub");
  if (subEl) subEl.textContent = sub || "";
}

function updateActuatorControls({stale, online, estop}) {
  const reachable = !!online && !stale;
  const onBtn = document.getElementById("powerOnBtn");
  const offBtn = document.getElementById("powerOffBtn");
  const lightToggle = document.getElementById("lightToggle");
  // Power controls publish a request to the backend. Do not lock them behind
  // telemetry freshness, because ESP/RPi status can briefly flap while the
  // operator still needs to send ON/OFF. E-stop still blocks ON; OFF stays
  // available so the car can always be shut down.
  if (onBtn) onBtn.disabled = _powerBusy || estop;
  if (offBtn) offBtn.disabled = _powerBusy;
  if (lightToggle) lightToggle.disabled = !reachable;
}

function renderMetricGrid(tel, rpiPayload, actuator) {
  const t = tel || {};
  const p = rpiPayload || {};
  const fsm = safeText(t.fsm_state);
  const theta = t.theta != null ? Number(t.theta).toFixed(2) + "°" : "-";
  const visionServo = t.servo_angle != null ? Number(t.servo_angle).toFixed(1) + "°" : "-";
  const frame = safeText(t.frame_num);
  const base = safeText(p.base_state || p.base);
  const rpiSteer = (p.steer_angle != null) ? Number(p.steer_angle).toFixed(1) + "°" : "-";
  const relay = p.relay_on ? "ON" : "OFF";
  const relayCls = p.relay_on ? "text-warn" : "";
  const mode = safeText(p.current_route_mode || t.route_mode);
  const modeCls = mode === "AUTO" ? "text-ok" : (mode === "-" ? "" : "text-warn");
  const route = safeText(t.route_id || "-");
  const objectState = safeText(t.object_state || p.object_state || "none");
  const objectError = t.object_detector_error || p.object_detector_error || "";
  const objectCls = objectError ? "text-bad" : (objectState === "near" ? "text-bad" : (objectState === "far" ? "text-warn" : "text-ok"));
  // actuator label tracks the active path (ESP USB vs RPi GPIO)
  const actMode = detectMode(p);
  const steerLabel = actMode.kind === "esp" ? "Steer (ESP)" : "Steer (actuator)";

  const cells = [
    {label: "FSM", value: fsm},
    {label: "Theta", value: theta},
    {label: "Servo (vision)", value: visionServo},
    {label: steerLabel, value: rpiSteer},
    {label: "Base", value: base},
    {label: "Object", value: objectError ? "error" : objectState, cls: objectCls},
    {label: "Relay", value: relay, cls: relayCls},
    {label: "Mode", value: mode, cls: modeCls},
    {label: "Frame", value: frame},
    {label: "Route", value: route, full: true},
  ];

  document.getElementById("metricGrid").innerHTML = `
    <div class="metric-section-label">Live Metrics</div>
    <div class="metric-grid">
      ${cells.map(c => `
        <div class="metric-cell ${c.full ? 'metric-cell-full' : ''}">
          <div class="metric-label">${c.label}</div>
          <div class="metric-value ${c.cls || ''}">${c.value}</div>
        </div>`).join("")}
    </div>`;
}

const _eventLog = [];
const MAX_EVENTS = 8;
function addEvent(kind, msg) {
  const ts = new Date().toLocaleTimeString();
  _eventLog.unshift({kind, msg, ts});
  if (_eventLog.length > MAX_EVENTS) _eventLog.pop();
  renderEventLog();
}
function renderEventLog() {
  document.getElementById("eventLog").innerHTML = `
    <div style="font-size:12px;color:#888;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:2px">Events</div>
    <div class="event-log">
      ${_eventLog.map(e => `<div class="event-item ${safeText(e.kind)}">[${safeText(e.ts)}] ${safeText(e.msg)}</div>`).join("")}
    </div>`;
}

let lastRouteId;
let _polling = false;
async function pollStatus() {
  if (document.hidden) return;  // skip polling while tab is backgrounded
  if (_polling) return;         // guard: don't stack requests if a poll is slow
  _polling = true;
  try {
    const r = await fetch("/route/script/status" + qp);
    if (r.ok) {
      const j = await r.json();
      const st = j.status || {};
      const wasRunning = isRunning;
      isRunning = !!st.running;
      currentRunningStep = st.current_step || 0;
      if (st.running) {
        const total = st.total || 0;
        const stepDur = (st.step && Number(st.step.duration_s)) || 0;
        const stepElapsed = Number(st.step_elapsed_s ?? st.elapsed_in_step_s ?? 0);
        const fillRatio = stepDur > 0 ? Math.min(1, stepElapsed / stepDur) : 0;
        setPill(st.paused ? "pill-paused" : "pill-running", `${st.paused ? "paused" : "running"} ${currentRunningStep}/${total}`);
        const action = st.step ? safeText(st.step.action) : "";
        const pauseReason = st.pause_reason === "object_detected"
          ? "object"
          : (st.pause_reason === "manual_override" ? "manual" : (st.pause_reason || "script"));
        runDetail.textContent = action
          ? `${action} · ${stepElapsed.toFixed(1)} / ${stepDur.toFixed(1)}s`
          : "";
        if (st.paused && action) {
          runDetail.textContent = `${action} - paused ${safeText(pauseReason)} - ${stepElapsed.toFixed(1)} / ${stepDur.toFixed(1)}s`;
        }
        const overallRatio = total ? ((currentRunningStep - 1 + fillRatio) / total) : 0;
        progressBar.style.width = (overallRatio * 100).toFixed(1) + "%";
        renderProgressSegments(total, currentRunningStep, fillRatio);
      } else if (st.last_error) {
        setPill("pill-error", "error");
        runDetail.textContent = st.last_error;
        progressBar.style.width = "0%";
        renderProgressSegments(0, 0, 0);
      } else {
        setPill("pill-idle", "idle");
        if (wasRunning) runDetail.textContent = "finished";
        progressBar.style.width = "0%";
        renderProgressSegments(0, 0, 0);
        currentRunningStep = 0;
      }
      if (wasRunning !== isRunning || st.running) render();
      if (wasRunning && !isRunning) {
        setTimeout(() => { refreshRoutes(); reloadStream(); }, 800);
      }
    }
  } catch (e) {}
  try {
    const r2 = await fetch(STATUS + qp);
    if (r2.ok) {
      const j2 = await r2.json();
      const tel = j2.telemetry || {};
      const rpi = j2.rpi_status || null;
      const rpiPayload = (rpi && rpi.payload) || {};
      const actuator = j2.actuator || null;

      renderStatusBar(rpi, actuator);
      renderMetricGrid(tel, rpiPayload, actuator);
      pushTelemetryHistory(tel);
      renderTrendChart();
      renderFsmStrip();
      renderHealthBanner(rpi);
      renderManualStatus(tel);

      const newRid = tel.route_id || null;
      if (lastRouteId !== undefined && lastRouteId !== newRid) {
        setTimeout(() => { refreshRoutes(); reloadStream(); }, 600);
      }
      lastRouteId = newRid;
    }
  } catch (e) {}
  finally { _polling = false; }
}

function reloadStream() {
  const img = document.getElementById("stream");
  if (!img) return;
  const base = STREAM + qp + (qp ? "&" : "?") + "_t=" + Date.now();
  img.src = base;
}
setInterval(pollStatus, 500);
render();
renderStatusBar(null);
renderMetricGrid({}, null);
renderEventLog();

const routesTbody = document.querySelector("#routesTable tbody");
const routeFilterChips = document.getElementById("routeFilterChips");
const routeSortSel = document.getElementById("routeSort");
const routeSearchInput = document.getElementById("routeSearch");
const routeSelectAll = document.getElementById("routeSelectAll");
const deleteSelectedBtn = document.getElementById("deleteSelected");
const compareSelectedBtn = document.getElementById("compareSelected");
let _routesAll = [];
let _routeFilter = "all";
const _selectedRoutes = new Set();
function fmtBytes(n) {
  if (n == null) return "-";
  if (n < 1024) return n + " B";
  if (n < 1024*1024) return (n/1024).toFixed(1) + " KB";
  return (n/1024/1024).toFixed(1) + " MB";
}
function fmtElapsed(s) {
  if (s == null) return "-";
  return Number(s).toFixed(1) + " s";
}
function fmtTs(t) {
  if (!t) return "-";
  return t.replace("T", " ").replace(/\.[0-9]+/, "").replace("+00:00", "Z");
}
async function refreshRoutes() {
  try {
    const r = await fetch("/routes/list?limit=50" + (TOKEN ? ("&token=" + encodeURIComponent(TOKEN)) : ""));
    if (!r.ok) return;
    const j = await r.json();
    _routesAll = j.routes || [];
    renderRoutes();
  } catch (e) {}
}

function renderRoutes() {
  const filtered = applyRouteFilters(_routesAll);
  routesTbody.innerHTML = "";
  filtered.forEach(rr => {
    const tr = document.createElement("tr");
    tr.className = "route-clickable";
    tr.dataset.name = rr.route_id;
    tr.appendChild(makeCheckCell(rr.route_id));
    tr.appendChild(makeTextCell(rr.route_id));
    tr.appendChild(makeTextCell(rr.route_mode || '-'));
    tr.appendChild(makePresetCell(rr));
    tr.appendChild(makeStatusCell(rr));
    tr.appendChild(makeTextCell(rr.total_frames != null ? String(rr.total_frames) : '-'));
    tr.appendChild(makeTextCell(fmtElapsed(rr.elapsed_s)));
    tr.appendChild(makeTextCell(fmtBytes(rr.zip_size)));
    tr.appendChild(makeTextCell(fmtTs(rr.end_timestamp_utc)));
    tr.appendChild(makeActionsCell(rr));
    routesTbody.appendChild(tr);
  });
  updateSelectionUI();
}

function makeTextCell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}
function makeCheckCell(routeId) {
  const td = document.createElement("td");
  const cb = document.createElement("input");
  cb.type = "checkbox"; cb.className = "route-pick";
  cb.dataset.name = routeId;
  cb.checked = _selectedRoutes.has(routeId);
  cb.addEventListener("click", e => e.stopPropagation());
  td.appendChild(cb);
  return td;
}
function makePresetCell(rr) {
  const td = document.createElement("td");
  if (rr.preset_name) {
    const span = document.createElement("span");
    span.className = "badge badge-ok";
    span.textContent = rr.preset_name;
    td.appendChild(span);
  } else if (rr.script_source) {
    const span = document.createElement("span");
    span.className = "muted";
    span.textContent = rr.script_source;
    td.appendChild(span);
  } else {
    const span = document.createElement("span");
    span.className = "muted";
    span.textContent = "-";
    td.appendChild(span);
  }
  return td;
}
function makeStatusCell(rr) {
  const td = document.createElement("td");
  const accepted = rr.accepted === false ? " ✗" : (rr.accepted === true ? " ✓" : "");
  td.textContent = (rr.status || '-') + accepted;
  return td;
}
function makeActionsCell(rr) {
  const td = document.createElement("td");
  if (rr.has_zip) {
    const a = document.createElement("a");
    a.className = "pill pill-running";
    a.style.cssText = "text-decoration:none;padding:4px 10px;margin-right:6px";
    a.href = `/routes/download/${encodeURIComponent(rr.route_id)}${qp}`;
    a.textContent = "⬇";
    a.addEventListener("click", e => e.stopPropagation());
    td.appendChild(a);
  } else {
    const span = document.createElement("span");
    span.className = "muted";
    span.style.marginRight = "6px";
    span.textContent = "no zip";
    td.appendChild(span);
  }
  const btn = document.createElement("button");
  btn.className = "rm route-del";
  btn.dataset.name = rr.route_id;
  btn.style.padding = "4px 10px";
  btn.textContent = "🗑";
  td.appendChild(btn);
  return td;
}

function applyRouteFilters(list) {
  let out = list.slice();
  if (_routeFilter === "accepted") out = out.filter(r => r.accepted === true);
  else if (_routeFilter === "rejected") out = out.filter(r => r.accepted === false);
  const q = (routeSearchInput && routeSearchInput.value || "").trim().toLowerCase();
  if (q) out = out.filter(r => (r.route_id || "").toLowerCase().includes(q));
  const sort = routeSortSel ? routeSortSel.value : "recent";
  if (sort === "longest") out.sort((a, b) => (b.elapsed_s || 0) - (a.elapsed_s || 0));
  else if (sort === "frames") out.sort((a, b) => (b.total_frames || 0) - (a.total_frames || 0));
  else out.sort((a, b) => (b.end_timestamp_utc || "").localeCompare(a.end_timestamp_utc || ""));
  return out;
}

function updateSelectionUI() {
  if (!deleteSelectedBtn) return;
  const n = _selectedRoutes.size;
  deleteSelectedBtn.disabled = n === 0;
  deleteSelectedBtn.textContent = n ? `🗑 selected (${n})` : "🗑 selected";
  if (compareSelectedBtn) {
    compareSelectedBtn.disabled = n < 2;
    compareSelectedBtn.textContent = n >= 2 ? `⊟ compare (${n})` : "⊟ compare";
  }
}

if (routeFilterChips) routeFilterChips.onclick = (e) => {
  if (!e.target.classList.contains("chip")) return;
  routeFilterChips.querySelectorAll(".chip").forEach(c => c.classList.toggle("active", c === e.target));
  _routeFilter = e.target.dataset.filter || "all";
  renderRoutes();
};
if (routeSortSel) routeSortSel.onchange = renderRoutes;
if (routeSearchInput) routeSearchInput.oninput = renderRoutes;
if (routeSelectAll) routeSelectAll.onchange = (e) => {
  const want = e.target.checked;
  applyRouteFilters(_routesAll).forEach(r => want ? _selectedRoutes.add(r.route_id) : _selectedRoutes.delete(r.route_id));
  renderRoutes();
};
if (deleteSelectedBtn) deleteSelectedBtn.onclick = async () => {
  const n = _selectedRoutes.size;
  if (!n) return;
  if (!confirm(`Delete ${n} selected route(s)? This removes directories + zips.`)) return;
  for (const id of Array.from(_selectedRoutes)) {
    try { await fetch(`/routes/${encodeURIComponent(id)}${qp}`, {method: "DELETE"}); } catch (e) {}
  }
  _selectedRoutes.clear();
  refreshRoutes();
};
document.getElementById("refreshRoutes").onclick = refreshRoutes;
setInterval(refreshRoutes, 5000);
refreshRoutes();

routesTbody.onclick = async (e) => {
  const t = e.target;
  if (t.classList.contains("route-del")) {
    const name = t.dataset.name;
    if (!confirm(`Delete route ${name}? This removes its directory and zip.`)) return;
    const r = await fetch(`/routes/${encodeURIComponent(name)}${qp}`, {method: "DELETE"});
    if (!r.ok) { alert("delete failed"); return; }
    refreshRoutes();
    return;
  }
  const tr = t.closest("tr.route-clickable");
  if (tr && tr.dataset.name) {
    openSummary(tr.dataset.name);
  }
};

async function openSummary(name) {
  const overview = document.getElementById("summaryOverview");
  const scriptDiv = document.getElementById("summaryScript");
  const jsonPre = document.getElementById("summaryJson");
  const dlBtn = document.getElementById("summaryDownload");
  const title = document.getElementById("summaryTitle");
  overview.innerHTML = `<div class="muted" style="margin-top:14px">loading…</div>`;
  scriptDiv.innerHTML = "";
  jsonPre.textContent = "";
  if (dlBtn) dlBtn.style.display = "none";
  title.textContent = `Route summary · ${name}`;
  document.getElementById("summaryModal").classList.add("show");
  // reset to Overview tab on open
  document.querySelectorAll(".modal-tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.mtab === "overview"));
  document.querySelectorAll(".tab-pane[data-mpane]").forEach(p => p.classList.toggle("active", p.dataset.mpane === "overview"));
  try {
    const r = await fetch(`/routes/${encodeURIComponent(name)}/summary${qp}`);
    if (!r.ok) {
      overview.innerHTML = `<div class="muted" style="margin-top:14px;color:var(--bad)">load failed (${r.status})</div>`;
      return;
    }
    const j = await r.json();
    const s = j.summary || {};
    overview.innerHTML = renderSummaryOverview(s);
    scriptDiv.innerHTML = renderSummaryScript(s);
    jsonPre.textContent = JSON.stringify(s, null, 2);
    if (dlBtn) {
      const matched = _routesAll.find(rr => rr.route_id === name);
      if (matched && matched.has_zip) {
        dlBtn.href = `/routes/download/${encodeURIComponent(name)}${qp}`;
        dlBtn.style.display = "";
      }
    }
  } catch (e) {
    overview.innerHTML = `<div class="muted" style="margin-top:14px;color:var(--bad)">network error</div>`;
  }
}

function renderSummaryOverview(s) {
  const accepted = s.accepted === true ? '<span class="badge badge-ok">accepted ✓</span>'
    : (s.accepted === false ? '<span class="badge badge-fail">rejected ✗</span>' : '-');
  return `
    <div class="kv">
      <div class="k">Route ID</div><div class="v">${safeText(s.route_id)}</div>
      <div class="k">Mode</div><div class="v">${safeText(s.route_mode)}</div>
      <div class="k">Status</div><div class="v">${safeText(s.status)} ${accepted}</div>
      <div class="k">Rejection reason</div><div class="v">${safeText(s.rejection_reason)}</div>
      <div class="k">Started (UTC)</div><div class="v">${safeText(s.start_timestamp_utc)}</div>
      <div class="k">Ended (UTC)</div><div class="v">${safeText(s.end_timestamp_utc)}</div>
      <div class="k">Elapsed</div><div class="v">${s.total_elapsed_seconds != null ? Number(s.total_elapsed_seconds).toFixed(2) + ' s' : '-'}</div>
      <div class="k">Total frames</div><div class="v">${s.total_frames ?? '-'}</div>
      <div class="k">Frames with theta</div><div class="v">${s.frames_with_theta ?? '-'}</div>
      <div class="k">Gap ratio</div><div class="v">${s.gap_ratio != null ? Number(s.gap_ratio).toFixed(3) : '-'}</div>
      <div class="k">HW errors</div><div class="v">${s.hardware_error_count ?? '-'}</div>
      <div class="k">Abstract steps</div><div class="v">${s.abstract_steps ?? '-'}</div>
    </div>`;
}

function renderSummaryScript(s) {
  const extra = s.extra_meta || {};
  const script = extra.script || {};
  const stepsRows = (script.steps || []).map((st, i) => {
    const d = Number(st.duration_s);
    const dTxt = Number.isFinite(d) ? d.toFixed(1) + " s" : "-";
    return `<tr><td>${i+1}</td><td>${safeText(st.action)}</td><td>${dTxt}</td></tr>`;
  }).join("");
  const stepsTable = stepsRows
    ? `<table style="margin-top:6px"><thead><tr><th>#</th><th>Action</th><th>Duration</th></tr></thead><tbody>${stepsRows}</tbody></table>`
    : `<div class="muted">no script steps recorded</div>`;
  let submittedAt = null;
  if (script.submitted_at_unix) {
    const ms = Number(script.submitted_at_unix) * 1000;
    if (Number.isFinite(ms)) {
      try { submittedAt = new Date(ms).toISOString(); } catch (e) { submittedAt = null; }
    }
  }
  return `
    <div class="kv">
      <div class="k">Source</div><div class="v">${safeText(script.source)}</div>
      <div class="k">Preset name</div><div class="v">${safeText(script.preset_name)}</div>
      <div class="k">Description</div><div class="v">${safeText(script.description)}</div>
      <div class="k">Submitted (UTC)</div><div class="v">${safeText(submittedAt)}</div>
      <div class="k">Step count</div><div class="v">${(script.steps || []).length}</div>
    </div>
    <h3>Steps</h3>
    ${stepsTable}`;
}

// modal tabs handler
document.querySelectorAll(".modal-tabs .tab").forEach(btn => {
  btn.onclick = () => {
    const target = btn.dataset.mtab;
    document.querySelectorAll(".modal-tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.mtab === target));
    document.querySelectorAll(".tab-pane[data-mpane]").forEach(p => p.classList.toggle("active", p.dataset.mpane === target));
  };
});

document.getElementById("summaryClose").onclick = () => document.getElementById("summaryModal").classList.remove("show");
document.getElementById("summaryModal").addEventListener("click", (e) => {
  if (e.target.id === "summaryModal") e.currentTarget.classList.remove("show");
});

document.getElementById("deleteAllRoutes").onclick = async () => {
  if (!confirm("Delete ALL routes (directories + zips)? This cannot be undone.")) return;
  if (!confirm("Are you really sure? This wipes recorded data.")) return;
  const r = await fetch("/routes/delete_all" + qp, {method: "POST"});
  const j = await r.json().catch(() => ({}));
  alert(`removed=${j.removed||0} errors=${(j.errors||[]).length}`);
  refreshRoutes();
};

// ---------- presets CRUD ----------
const presetSelect = document.getElementById("presetSelect");
const presetName = document.getElementById("presetName");
async function refreshPresets() {
  try {
    const r = await fetch("/presets" + qp);
    if (!r.ok) return;
    const j = await r.json();
    const list = j.presets || [];
    const cur = presetSelect.value;
    presetSelect.innerHTML = "<option value=\"\">— select preset —</option>";
    list.forEach(p => {
      const o = document.createElement("option");
      o.value = p.name;
      o.textContent = `${p.name} (${p.steps_count} steps)`;
      presetSelect.appendChild(o);
    });
    if (cur) presetSelect.value = cur;
  } catch (e) {}
}
document.getElementById("presetLoad").onclick = async () => {
  const name = presetSelect.value;
  if (!name) { alert("select a preset first"); return; }
  const r = await fetch(`/presets/${encodeURIComponent(name)}${qp}`);
  if (!r.ok) { alert("load failed"); return; }
  const j = await r.json();
  const p = j.preset || {};
  steps.length = 0;
  (p.steps || []).forEach(s => steps.push({action: s.action, duration_s: Number(s.duration_s)}));
  presetName.value = p.name || name;
  resetEditor();
  render();
};
document.getElementById("presetSave").onclick = async () => {
  const name = (presetName.value || "").trim();
  if (!name) { alert("enter preset name"); return; }
  if (steps.length === 0) { alert("add steps first"); return; }
  const r = await fetch(`/presets/${encodeURIComponent(name)}${qp}`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({steps})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { alert("save failed: " + JSON.stringify(j.detail || r.status)); return; }
  await refreshPresets();
  presetSelect.value = name;
};
document.getElementById("presetDelete").onclick = async () => {
  const name = presetSelect.value;
  if (!name) { alert("select a preset first"); return; }
  if (!confirm(`Delete preset "${name}"?`)) return;
  const r = await fetch(`/presets/${encodeURIComponent(name)}${qp}`, {method: "DELETE"});
  if (!r.ok) { alert("delete failed"); return; }
  presetSelect.value = "";
  refreshPresets();
};
refreshPresets();
setInterval(refreshPresets, 10000);

// ---------- tune panel ----------
const tuneFields = document.getElementById("tuneFields");
const tuneStatus = document.getElementById("tuneStatus");
const tuneApplyBtn = document.getElementById("tuneApply");
const tuneSaveBtn = document.getElementById("tuneSave");
const tuneResetSavedBtn = document.getElementById("tuneResetSaved");
const tuneResetDefaultsBtn = document.getElementById("tuneResetDefaults");
let tuneState = null;
let tuneDraft = {};
let tuneTouched = false;
let tuneEditing = false;

function tuneSame(a, b, schema) {
  if (!a || !b || !schema) return false;
  return schema.every(s => Math.abs(Number(a[s.key]) - Number(b[s.key])) <= 1e-9);
}

function tuneFmt(value, spec) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return spec.type === "int" ? String(Math.round(n)) : n.toFixed(spec.step < 0.1 ? 2 : 1);
}

function tuneLocalDirty() {
  return !!(tuneState && tuneTouched && !tuneSame(tuneDraft, tuneState.values, tuneState.schema));
}

function renderTuneStatus() {
  if (!tuneStatus || !tuneState) return;
  const idle = tuneState.idle || {};
  const saved = tuneState.saved || {};
  const localDirty = tuneLocalDirty();
  const parts = [
    idle.ok ? "idle" : safeText(idle.reason || "blocked"),
    localDirty ? "staged changes" : (tuneState.dirty ? "unsaved active values" : "active saved/default"),
    saved.exists ? `saved ${safeText(saved.updated_utc || "")}` : "not saved",
  ];
  tuneStatus.textContent = parts.filter(Boolean).join(" · ");
  if (tuneApplyBtn) tuneApplyBtn.disabled = !idle.ok || !localDirty;
  if (tuneSaveBtn) tuneSaveBtn.disabled = localDirty;
  if (tuneResetSavedBtn) tuneResetSavedBtn.disabled = !idle.ok || !saved.exists;
  if (tuneResetDefaultsBtn) tuneResetDefaultsBtn.disabled = !idle.ok;
}

function renderTuneFields() {
  if (!tuneFields || !tuneState) return;
  const byGroup = {};
  (tuneState.schema || []).forEach(spec => {
    if (!byGroup[spec.group]) byGroup[spec.group] = [];
    byGroup[spec.group].push(spec);
  });
  let html = "";
  Object.keys(byGroup).forEach(group => {
    html += `<h3>${safeText(group)}</h3>`;
    byGroup[group].forEach(spec => {
      const key = spec.key;
      const value = tuneDraft[key] ?? tuneState.values[key];
      html += `
        <div class="tune-field" data-key="${safeText(key)}">
          <label for="tune_${safeText(key)}">${safeText(spec.label)}</label>
          <input id="tune_${safeText(key)}_range" data-tune-input="1" data-key="${safeText(key)}" type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${safeText(value)}">
          <input id="tune_${safeText(key)}" data-tune-input="1" data-key="${safeText(key)}" type="number" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${safeText(value)}">
          <div class="tune-meta">current ${safeText(tuneFmt(tuneState.values[key], spec))} · default ${safeText(tuneFmt(tuneState.defaults[key], spec))}</div>
        </div>`;
    });
  });
  tuneFields.innerHTML = html;
  renderTuneStatus();
}

function tuneInputFocused() {
  return !!(tuneFields && document.activeElement && tuneFields.contains(document.activeElement));
}

async function refreshTune(forceRender) {
  if (!tuneFields) return;
  try {
    const r = await fetch("/api/tune" + qp);
    if (!r.ok) {
      if (tuneStatus) tuneStatus.textContent = "tune unavailable";
      return;
    }
    const data = await r.json();
    tuneState = data;
    const shouldRenderFields = forceRender || !tuneFields.hasChildNodes();
    if (shouldRenderFields && !tuneEditing && !tuneInputFocused()) {
      tuneDraft = Object.assign({}, data.values || {});
      tuneTouched = false;
      renderTuneFields();
    } else {
      renderTuneStatus();
    }
  } catch (e) {
    if (tuneStatus) tuneStatus.textContent = "tune network error";
  }
}

function updateTunePair(key, value) {
  document.querySelectorAll(`[data-tune-input][data-key="${key}"]`).forEach(input => {
    if (input === document.activeElement && input.type === "number") return;
    input.value = String(value);
  });
}

async function tunePost(path, body, method) {
  try {
    const r = await fetch(path + qp, {
      method: method || "POST",
      headers: {"Content-Type": "application/json"},
      body: body == null ? undefined : JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (tuneStatus) tuneStatus.textContent = "tune error: " + (j.detail || r.status);
      return;
    }
    tuneState = j;
    tuneDraft = Object.assign({}, j.values || {});
    tuneTouched = false;
    renderTuneFields();
  } catch (e) {
    if (tuneStatus) tuneStatus.textContent = "tune network error";
  }
}

if (tuneFields) {
  tuneFields.addEventListener("focusin", e => {
    if (e.target && e.target.dataset && e.target.dataset.tuneInput) tuneEditing = true;
  });
  tuneFields.addEventListener("focusout", () => {
    setTimeout(() => {
      tuneEditing = !!tuneInputFocused();
    }, 0);
  });
  tuneFields.addEventListener("input", e => {
    const input = e.target;
    if (!input || !input.dataset || !input.dataset.key || !tuneState) return;
    const key = input.dataset.key;
    const spec = (tuneState.schema || []).find(s => s.key === key);
    if (!spec) return;
    const n = Number(input.value);
    if (!Number.isFinite(n)) return;
    const value = spec.type === "int" ? Math.round(n) : n;
    tuneDraft[key] = value;
    tuneTouched = true;
    updateTunePair(key, value);
    renderTuneStatus();
  });
}
if (tuneApplyBtn) tuneApplyBtn.onclick = () => tunePost("/api/tune", {values: tuneDraft}, "PUT");
if (tuneSaveBtn) tuneSaveBtn.onclick = () => tunePost("/api/tune/save");
if (tuneResetSavedBtn) tuneResetSavedBtn.onclick = () => tunePost("/api/tune/reset", {target: "saved"});
if (tuneResetDefaultsBtn) tuneResetDefaultsBtn.onclick = () => tunePost("/api/tune/reset", {target: "defaults"});
refreshTune(true);
setInterval(() => refreshTune(false), 1000);

// §13 ─ Telemetry trends (F1 chart), FSM strip (F2), health banner (F3) ──
const _telHistory = [];           // {t, theta, servo, fsm}
const TEL_HISTORY_MS = 30000;     // keep last 30s
const FSM_COLORS = {
  GAPPING: "#6b6f78",
  DANGER_LEFT: "#f87171",
  DANGER_RIGHT: "#fbbf24",
  TRACKING_COAST: "#60a5fa",
  TRACKING_PD: "#4ade80",
};

function pushTelemetryHistory(tel) {
  const now = Date.now();
  _telHistory.push({
    t: now,
    theta: (tel && tel.theta != null) ? Number(tel.theta) : null,
    servo: (tel && tel.servo_angle != null) ? Number(tel.servo_angle) : null,
    fsm: (tel && tel.fsm_state) || null,
  });
  const cutoff = now - TEL_HISTORY_MS;
  while (_telHistory.length && _telHistory[0].t < cutoff) _telHistory.shift();
}

function _sparkPath(points, getY, x0, span, yMin, yMax, h) {
  if (points.length < 2 || yMax === yMin) return "";
  return points.map((p, i) => {
    const x = ((p.t - x0) / span) * 100;
    const y = h - ((getY(p) - yMin) / (yMax - yMin)) * h;
    return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function renderTrendChart() {
  const el = document.getElementById("trendChart");
  if (!el) return;
  const pts = _telHistory.filter(p => p.theta != null || p.servo != null);
  if (pts.length < 2) { el.innerHTML = '<div class="muted" style="font-size:11px">collecting…</div>'; return; }
  const x0 = pts[0].t;
  const span = Math.max(1, pts[pts.length - 1].t - x0);
  const h = 48;
  const thetaPts = pts.filter(p => p.theta != null);
  const servoPts = pts.filter(p => p.servo != null);
  // shared Y scale across both series so they're comparable
  const allY = pts.flatMap(p => [p.theta, p.servo]).filter(v => v != null);
  const yMin = Math.min(...allY), yMax = Math.max(...allY);
  const thetaPath = _sparkPath(thetaPts, p => p.theta, x0, span, yMin, yMax, h);
  const servoPath = _sparkPath(servoPts, p => p.servo, x0, span, yMin, yMax, h);
  el.innerHTML = `
    <div class="metric-section-label">Theta / Servo (last 30s)</div>
    <svg viewBox="0 0 100 ${h}" preserveAspectRatio="none" class="trend-svg">
      <path d="${servoPath}" fill="none" stroke="var(--warn)" stroke-width="0.8" vector-effect="non-scaling-stroke"/>
      <path d="${thetaPath}" fill="none" stroke="var(--info)" stroke-width="0.8" vector-effect="non-scaling-stroke"/>
    </svg>
    <div class="trend-legend"><span class="text-warn">■ servo</span> <span style="color:var(--info)">■ theta</span> <span class="muted">${yMin.toFixed(0)}…${yMax.toFixed(0)}°</span></div>`;
}

function renderFsmStrip() {
  const el = document.getElementById("fsmStrip");
  if (!el) return;
  const pts = _telHistory.filter(p => p.fsm);
  if (!pts.length) { el.innerHTML = ""; return; }
  const x0 = pts[0].t;
  const span = Math.max(1, Date.now() - x0);
  let segs = "";
  for (let i = 0; i < pts.length; i++) {
    const start = pts[i].t;
    const end = (i + 1 < pts.length) ? pts[i + 1].t : Date.now();
    const left = ((start - x0) / span) * 100;
    const w = ((end - start) / span) * 100;
    const color = FSM_COLORS[pts[i].fsm] || "#444";
    segs += `<div class="fsm-seg" style="left:${left.toFixed(2)}%;width:${w.toFixed(2)}%;background:${color}" title="${safeText(pts[i].fsm)}"></div>`;
  }
  const cur = pts[pts.length - 1].fsm;
  el.innerHTML = `
    <div class="metric-section-label">FSM state (last 30s) · now: <span style="color:${FSM_COLORS[cur] || '#888'}">${safeText(cur)}</span></div>
    <div class="fsm-track">${segs}</div>`;
}

function renderHealthBanner(rpi) {
  const el = document.getElementById("healthBanner");
  if (!el) return;
  const payload = (rpi && rpi.payload) || {};
  const stale = !rpi || !!rpi.stale;
  const estop = !!payload.estop_active;
  const objectNear = !!payload.object_pause_active || payload.object_state === "near";
  const objectError = payload.object_detector_error || "";
  if (estop) {
    el.className = "health-banner show bad";
    el.innerHTML = "";
    const span = document.createElement("span");
    span.textContent = "⛔ E-STOP ACTIVE — vehicle latched safe. ";
    const btn = document.createElement("button");
    btn.textContent = "Reset E-stop";
    btn.className = "estop-reset-btn";
    btn.onclick = estopReset;
    el.appendChild(span);
    el.appendChild(btn);
  } else if (objectNear) {
    el.className = "health-banner show bad";
    el.textContent = "Object near - route paused, base stopped, servo released.";
  } else if (objectError) {
    el.className = "health-banner show warn";
    el.textContent = "Object detector error - " + objectError;
  } else if (stale) {
    el.className = "health-banner show warn";
    el.textContent = "⚠ Actuator telemetry stale — MQTT peer offline or connection lost.";
  } else {
    el.className = "health-banner";
    el.textContent = "";
  }
}

async function estopReset() {
  if (!confirm("Reset E-stop? This only clears if the physical button is released (safe).")) return;
  try {
    const r = await fetch("/control/estop_reset" + qp, {method: "POST"});
    if (!r.ok) { alert("reset request failed: " + r.status); return; }
    addEvent("info", "E-stop reset requested");
  } catch (e) {
    alert("reset network error");
  }
}

if (compareSelectedBtn) compareSelectedBtn.onclick = async () => {
  const ids = Array.from(_selectedRoutes).slice(0, 3);
  if (ids.length < 2) return;
  await openCompare(ids);
};

async function openCompare(ids) {
  const overview = document.getElementById("summaryOverview");
  const scriptDiv = document.getElementById("summaryScript");
  const jsonPre = document.getElementById("summaryJson");
  const dlBtn = document.getElementById("summaryDownload");
  const title = document.getElementById("summaryTitle");
  if (dlBtn) dlBtn.style.display = "none";
  title.textContent = `Compare ${ids.length} routes`;
  scriptDiv.innerHTML = '<div class="muted">comparison uses the Overview tab</div>';
  jsonPre.textContent = "";
  overview.innerHTML = '<div class="muted" style="margin-top:14px">loading…</div>';
  document.getElementById("summaryModal").classList.add("show");
  document.querySelectorAll(".modal-tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.mtab === "overview"));
  document.querySelectorAll(".tab-pane[data-mpane]").forEach(p => p.classList.toggle("active", p.dataset.mpane === "overview"));
  try {
    const summaries = await Promise.all(ids.map(async id => {
      try {
        const r = await fetch(`/routes/${encodeURIComponent(id)}/summary${qp}`);
        if (!r.ok) return {route_id: id, _error: r.status};
        const j = await r.json();
        return j.summary || {route_id: id};
      } catch (e) { return {route_id: id, _error: "net"}; }
    }));
    overview.innerHTML = renderCompare(summaries);
    jsonPre.textContent = JSON.stringify(summaries, null, 2);
  } catch (e) {
    overview.innerHTML = '<div class="muted" style="margin-top:14px;color:var(--bad)">compare failed</div>';
  }
}

function renderCompare(summaries) {
  const rows = [
    ["Mode", s => safeText(s.route_mode)],
    ["Status", s => safeText(s.status)],
    ["Accepted", s => s.accepted === true ? "✓" : (s.accepted === false ? "✗" : "-")],
    ["Elapsed", s => s.total_elapsed_seconds != null ? Number(s.total_elapsed_seconds).toFixed(2) + " s" : "-"],
    ["Frames", s => s.total_frames ?? "-"],
    ["Frames w/ theta", s => s.frames_with_theta ?? "-"],
    ["Gap ratio", s => s.gap_ratio != null ? Number(s.gap_ratio).toFixed(3) : "-"],
    ["HW errors", s => s.hardware_error_count ?? "-"],
  ];
  const head = `<th>Metric</th>` + summaries.map(s => `<th>${safeText(s.route_id)}</th>`).join("");
  const body = rows.map(([label, fn]) =>
    `<tr><td class="cmp-metric">${label}</td>` + summaries.map(s => `<td>${s._error ? '<span class="text-bad">err</span>' : fn(s)}</td>`).join("") + `</tr>`
  ).join("");
  return `<table class="data-table cmp-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}


})();
