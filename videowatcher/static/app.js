"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { cameras: [], camera: null, results: [], editing: null, poll: null };

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-Videowatcher"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

const fmtSize = (n) => !n ? "?" : n >= 1 << 30 ? `${(n / 2 ** 30).toFixed(2)} GB` : `${(n / 2 ** 20).toFixed(1)} MB`;
const timeOf = (s) => s.slice(11);

// -- session --------------------------------------------------------------------

function showLogin() {
  clearInterval(state.poll);
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
}

async function showApp(username) {
  $("#whoami").textContent = username;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#search-form").date.valueAsDate = new Date();
  await loadCameras();
  refreshDownloads();
  state.poll = setInterval(refreshDownloads, 1500);
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#login-error").textContent = "";
  try {
    const me = await api("/api/login", { method: "POST", body: { username: f.username.value, password: f.password.value } });
    f.password.value = "";
    showApp(me.username);
  } catch (err) { $("#login-error").textContent = err.message; }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

// -- cameras --------------------------------------------------------------------

async function loadCameras() {
  state.cameras = await api("/api/cameras");
  if (state.camera) state.camera = state.cameras.find((c) => c.id === state.camera.id) || null;
  if (!state.camera && state.cameras.length) state.camera = state.cameras[0];
  renderCameras();
}

function renderCameras() {
  const list = $("#camera-list");
  list.replaceChildren(...state.cameras.map((cam) => {
    const edit = el("span", { className: "edit", textContent: "edit" });
    edit.addEventListener("click", (ev) => { ev.stopPropagation(); openCameraForm(cam); });
    const where = cam.transport === "p2p" ? `P2P ${cam.serial}` : `${cam.host}:${cam.port}`;
    const li = el("li", { className: cam.id === state.camera?.id ? "active" : "", title: where },
      el("span", { textContent: cam.name }), edit);
    li.addEventListener("click", () => { state.camera = cam; renderCameras(); clearResults(); });
    return li;
  }));
  if (!state.cameras.length) list.append(el("li", { className: "muted", textContent: "No cameras yet" }));

  const cam = state.camera;
  $("#search-title").textContent = cam ? `Recordings: ${cam.name}` : "Recordings";
  const channel = $("#search-form").channel;
  channel.replaceChildren(...Array.from({ length: cam?.channels || 1 }, (_, i) => el("option", { value: i, textContent: i + 1 })));
  for (const b of $("#search-form").querySelectorAll("button")) b.disabled = !cam;
}

function openCameraForm(cam) {
  const f = $("#camera-form");
  state.editing = cam || null;
  f.reset();
  $("#camera-form-title").textContent = cam ? `Edit ${cam.name}` : "Add camera";
  $("#camera-delete").hidden = !cam;
  $("#camera-error").textContent = "";
  if (cam) for (const k of ["name", "host", "port", "username", "channels", "transport", "serial"]) f[k].value = cam[k];
  f.password.placeholder = cam?.has_password ? "leave blank to keep" : "";
  f.hidden = false;
  f.name.focus();
}

$("#add-camera").addEventListener("click", () => openCameraForm(null));
$("#camera-cancel").addEventListener("click", () => { $("#camera-form").hidden = true; });

$("#camera-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    name: f.name.value.trim(), host: f.host.value.trim(), port: Number(f.port.value),
    username: f.username.value.trim(), password: f.password.value || null,
    channels: Number(f.channels.value), transport: f.transport.value, serial: f.serial.value.trim(),
  };
  try {
    const saved = state.editing
      ? await api(`/api/cameras/${state.editing.id}`, { method: "PUT", body })
      : await api("/api/cameras", { method: "POST", body });
    state.camera = saved;
    f.hidden = true;
    await loadCameras();
  } catch (err) { $("#camera-error").textContent = err.message; }
});

$("#camera-delete").addEventListener("click", async () => {
  const cam = state.editing;
  if (!cam || !confirm(`Delete camera "${cam.name}"? Downloaded files are kept.`)) return;
  await api(`/api/cameras/${cam.id}`, { method: "DELETE" });
  if (state.camera?.id === cam.id) state.camera = null;
  $("#camera-form").hidden = true;
  clearResults();
  await loadCameras();
});

$("#test-camera").addEventListener("click", async () => {
  const status = $("#search-status");
  status.textContent = "Connecting…";
  try {
    const info = await api(`/api/cameras/${state.camera.id}/test`, { method: "POST" });
    status.textContent = `Connected: ${info.DeviceModel || "camera"} ${info.SerialNo || ""} (firmware ${info.SoftWareVersion || "?"})`;
  } catch (err) { status.textContent = `Connection failed: ${err.message}`; }
});

// -- search ---------------------------------------------------------------------

function clearResults() {
  state.results = [];
  renderResults();
  $("#search-status").textContent = "";
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const status = $("#search-status");
  status.textContent = "Searching…";
  const q = new URLSearchParams({ date: f.date.value, from: f.from.value, to: f.to.value, channel: f.channel.value });
  try {
    state.results = await api(`/api/cameras/${state.camera.id}/recordings?${q}`);
    status.textContent = `${state.results.length} recording(s) found`;
  } catch (err) {
    state.results = [];
    status.textContent = `Search failed: ${err.message}`;
  }
  renderResults();
});

function renderResults() {
  const rows = state.results.map((r, i) => el("tr", {},
    el("td", {}, el("input", { type: "checkbox", value: i, className: "pick" })),
    el("td", { textContent: timeOf(r.begin) }),
    el("td", { textContent: timeOf(r.end) }),
    el("td", { textContent: fmtSize(r.size_bytes) }),
    el("td", { className: "file", textContent: r.file_name })));
  $("#results").replaceChildren(...rows);
  $("#select-all").checked = false;
  updateDownloadButton();
}

const picked = () => [...document.querySelectorAll(".pick:checked")].map((c) => state.results[c.value]);
const updateDownloadButton = () => { $("#download-selected").disabled = !picked().length; };

$("#results").addEventListener("change", updateDownloadButton);
$("#select-all").addEventListener("change", (e) => {
  for (const c of document.querySelectorAll(".pick")) c.checked = e.target.checked;
  updateDownloadButton();
});

$("#download-selected").addEventListener("click", async () => {
  const mp4 = $("#want-mp4").checked;
  for (const r of picked()) {
    await api("/api/downloads", { method: "POST", body: { camera_id: state.camera.id, mp4, ...r } })
      .catch((err) => alert(`Could not queue ${r.file_name}: ${err.message}`));
  }
  refreshDownloads();
});

// -- downloads ------------------------------------------------------------------

async function refreshDownloads() {
  let jobs;
  try { jobs = await api("/api/downloads"); } catch { return; }
  $("#downloads").replaceChildren(...jobs.map(renderJob));
  if (!jobs.length) $("#downloads").append(el("li", { className: "muted", textContent: "Nothing downloaded yet" }));
}

function renderJob(j) {
  const pct = j.progress ?? (j.state === "done" ? 100 : 0);
  const label = j.state === "downloading"
    ? `downloading ${fmtSize(j.received)}${j.expected ? ` of ${fmtSize(j.expected)}` : ""}`
    : j.state;
  const actions = el("div", { className: "actions" });
  if (j.has_mp4) actions.append(el("a", { href: `/api/downloads/${j.id}/file?kind=mp4`, textContent: "MP4" }));
  if (j.has_raw) actions.append(el("a", { href: `/api/downloads/${j.id}/file?kind=raw`, textContent: "original .h264" }));
  if (j.state === "queued" || j.state === "downloading") {
    const cancel = el("a", { href: "#", textContent: "cancel" });
    cancel.addEventListener("click", (e) => { e.preventDefault(); api(`/api/downloads/${j.id}`, { method: "DELETE" }).then(refreshDownloads); });
    actions.append(cancel);
  }
  return el("li", { className: `state-${j.state}` },
    el("div", { textContent: `${j.camera} · ${j.begin} → ${timeOf(j.end)} · ${label}` }),
    el("div", { className: "bar" }, el("span", { style: `width:${pct}%` })),
    j.error ? el("div", { className: "error", textContent: j.error }) : null,
    actions);
}

// -- boot -----------------------------------------------------------------------

api("/api/me").then((me) => showApp(me.username)).catch(() => showLogin());
