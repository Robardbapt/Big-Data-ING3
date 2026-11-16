// ConStruct-AI — viewer 3D du jumeau numérique (Three.js)
import * as THREE from 'three';
import { OrbitControls } from '/web/vendor/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from '/web/vendor/CSS2DRenderer.js';

const $ = (s) => document.querySelector(s);
const site = await fetch('/api/site').then((r) => r.json());
const plan = site.plan;
const START = new Date(site.start);
const ROLE_COLORS = { macon: 0x3987e5, manoeuvre: 0xd95926, chef: 0x199e70, '?': 0x9a9a9a };
const ROLE_CSS = { macon: '#3987e5', manoeuvre: '#d95926', chef: '#199e70', '?': '#9a9a9a' };
const BONES = [[0, 1], [1, 2], [1, 3], [2, 4], [4, 6], [3, 5], [5, 7], [1, 8], [8, 9], [8, 10], [9, 11], [11, 13], [10, 12], [12, 14]];

// ------------------------------------------------------------------ scène
const container = $('#scene');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
container.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(innerWidth, innerHeight);
Object.assign(labelRenderer.domElement.style, { position: 'absolute', top: '0', pointerEvents: 'none' });
container.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a2029);
scene.fog = new THREE.Fog(0x1a2029, 45, 90);
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.05, 300);
camera.position.set(-11, 11, -15);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.5, 0);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.49;

scene.add(new THREE.HemisphereLight(0xcfd8e6, 0x3a3127, 1.1));
const sun = new THREE.DirectionalLight(0xfff1dc, 2.2);
sun.position.set(-14, 24, -10);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -20, right: 20, top: 20, bottom: -20, near: 1, far: 70 });
scene.add(sun);

// sol, dalle, clôture
const ground = new THREE.Mesh(new THREE.PlaneGeometry(plan.width, plan.depth),
  new THREE.MeshStandardMaterial({ color: 0x5b5043, roughness: 1 }));
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);
const grid = new THREE.GridHelper(Math.max(plan.width, plan.depth), Math.max(plan.width, plan.depth), 0x6f6455, 0x655a4c);
grid.position.y = 0.005;
grid.material.transparent = true; grid.material.opacity = 0.35;
scene.add(grid);
const [hx0, hz0, hx1, hz1] = plan.house;
const slab = new THREE.Mesh(new THREE.BoxGeometry(hx1 - hx0 + 0.6, 0.12, hz1 - hz0 + 0.6),
  new THREE.MeshStandardMaterial({ color: 0x9a978f, roughness: 0.9 }));
slab.position.set((hx0 + hx1) / 2, -0.04, (hz0 + hz1) / 2);
slab.receiveShadow = true;
scene.add(slab);
{
  const w = plan.width / 2 - 0.3, d = plan.depth / 2 - 0.3;
  const pts = [[-w, -d], [w, -d], [w, d], [-w, d], [-w, -d]].map(([x, z]) => new THREE.Vector3(x, 1, z));
  const fence = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xf5b301 }));
  scene.add(fence);
}

function box(w, h, d, color, x, y, z, opts = {}) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color, roughness: 0.85, ...opts }));
  m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true;
  scene.add(m); return m;
}
function label(text, x, y, z, color = '#f5b301') {
  const div = document.createElement('div');
  div.className = 'label3d'; div.style.borderColor = color; div.textContent = text;
  const o = new CSS2DObject(div); o.position.set(x, y, z); scene.add(o); return o;
}
// palettes de parpaings
for (const [name, [px, pz]] of Object.entries(plan.pallets)) {
  box(1.2, 0.12, 1.0, 0x8a6a3e, px, 0.06, pz);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) for (let k = 0; k < 4; k++)
    box(0.48, 0.19, 0.19, 0xa9a69c, px - 0.25 + j * 0.5, 0.22 + i * 0.2, pz - 0.3 + k * 0.2);
  label(`Palette ${name}`, px, 1.2, pz);
}
// bétonnière
{
  const [mx, mz] = plan.mixer;
  box(1.2, 0.5, 0.9, 0x3a3f46, mx, 0.25, mz);
  const drum = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.5, 0.9, 20), new THREE.MeshStandardMaterial({ color: 0xd95926 }));
  drum.position.set(mx, 0.95, mz); drum.rotation.z = 0.6; drum.castShadow = true; scene.add(drum);
  label('Bétonnière', mx, 1.8, mz);
}
// base vie
{
  const [cx, cz] = plan.cabin;
  box(5, 2.5, 2.4, 0xdedad0, cx, 1.25, cz);
  box(5.2, 0.12, 2.6, 0x3b414a, cx, 2.56, cz);
  label('Base vie', cx, 3.0, cz);
}
// caméras fixes (mâts + champ de vision)
const camLayer = new THREE.Group(); scene.add(camLayer);
for (const c of plan.cameras) {
  box(0.12, c.y, 0.12, 0x555b63, c.x, c.y / 2, c.z);
  const head = box(0.35, 0.25, 0.5, 0x22262c, c.x, c.y + 0.1, c.z);
  head.rotation.y = c.yaw;
  const r = 26, pts = [];
  for (const a of [-c.fov / 2, c.fov / 2]) {
    pts.push(new THREE.Vector3(c.x, c.y, c.z), new THREE.Vector3(c.x + Math.sin(c.yaw + a) * r, 0, c.z + Math.cos(c.yaw + a) * r));
  }
  camLayer.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x9ec5f4, transparent: true, opacity: 0.5 })));
  const l = label(c.id, c.x, c.y + 0.8, c.z, '#9ec5f4'); camLayer.add(l);
}
camLayer.visible = false;

// ------------------------------------------------------------------ BIM & parpaings
const slots = plan.slots;
const slotIndex = new Map(slots.map((s, i) => [s.id, i]));
const blockGeo = new THREE.BoxGeometry(1, 1, 1);
const ghost = new THREE.InstancedMesh(blockGeo, new THREE.MeshBasicMaterial({ color: 0x5fb3ff, transparent: true, opacity: 0.08, depthWrite: false }), slots.length);
const built = new THREE.InstancedMesh(blockGeo, new THREE.MeshStandardMaterial({ roughness: 0.95 }), slots.length);
built.castShadow = true; built.receiveShadow = true;
const M = new THREE.Matrix4(), Q = new THREE.Quaternion(), S = new THREE.Vector3(), P = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0);
const BLOCK_GREY = new THREE.Color(0xb9b5a8), FRESH = new THREE.Color(0xf5b301);
function slotMatrix(s, scale) {
  Q.setFromAxisAngle(UP, s.rot);
  S.set(s.length * 0.985 * scale, plan.block.h * 0.97 * scale, plan.block.w * scale);
  P.set(s.x, s.y, s.z);
  return M.compose(P, Q, S);
}
slots.forEach((s, i) => {
  ghost.setMatrixAt(i, slotMatrix(s, 1));
  built.setMatrixAt(i, slotMatrix(s, 0));
  built.setColorAt(i, BLOCK_GREY);
});
scene.add(ghost, built);

let blocks = [];           // [{slot, t, badge}] trié par t
const shown = new Uint8Array(slots.length);
const freshUntil = new Float64Array(slots.length);
function applyBlocks(t, realNow) {
  let changed = false;
  for (let i = 0; i < blocks.length; i++) {
    const idx = slotIndex.get(blocks[i].slot);
    if (idx === undefined) continue;
    const want = blocks[i].t <= t ? 1 : 0;
    if (want !== shown[idx]) {
      shown[idx] = want;
      built.setMatrixAt(idx, slotMatrix(slots[idx], want));
      if (want) { freshUntil[idx] = realNow + 1500; built.setColorAt(idx, FRESH); }
      changed = true;
    }
  }
  for (let i = 0; i < slots.length; i++) {
    if (freshUntil[i] && realNow > freshUntil[i]) { freshUntil[i] = 0; built.setColorAt(i, BLOCK_GREY); changed = true; }
  }
  if (changed) { built.instanceMatrix.needsUpdate = true; built.instanceColor.needsUpdate = true; }
}

// ------------------------------------------------------------------ ouvriers
const workers = new Map();
const jointGeo = new THREE.SphereGeometry(0.045, 10, 8);
const boneGeo = new THREE.CylinderGeometry(0.05, 0.05, 1, 8); boneGeo.translate(0, 0.5, 0);
const headGeo = new THREE.SphereGeometry(0.11, 14, 10);
const helmetGeo = new THREE.SphereGeometry(0.13, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2);
const trailLayer = new THREE.Group(); trailLayer.visible = false; scene.add(trailLayer);

function makeWorker(badge) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x9a9a9a, roughness: 0.6, transparent: true });
  const vest = new THREE.MeshStandardMaterial({ color: 0xdfe94a, roughness: 0.6, emissive: 0x2a2c05, transparent: true });
  const joints = []; const bones = [];
  for (let i = 0; i < 15; i++) { const m = new THREE.Mesh(jointGeo, mat); m.castShadow = true; g.add(m); joints.push(m); }
  BONES.forEach(([a, b], k) => { const m = new THREE.Mesh(boneGeo, k === 7 ? vest : mat); m.castShadow = true; g.add(m); bones.push(m); });
  const head = new THREE.Mesh(headGeo, new THREE.MeshStandardMaterial({ color: 0xd9b38c, transparent: true })); g.add(head);
  const helmet = new THREE.Mesh(helmetGeo, new THREE.MeshStandardMaterial({ color: 0xf5b301, roughness: 0.4 })); g.add(helmet);
  // objets portés
  const carried = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.2, 0.2), new THREE.MeshStandardMaterial({ color: 0xb9b5a8 }));
  const barrow = new THREE.Group();
  const tray = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.25, 0.8), new THREE.MeshStandardMaterial({ color: 0x2f7d4f, metalness: 0.3 }));
  tray.position.set(0, 0.45, 0); barrow.add(tray);
  const wheel = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.05, 8, 16), new THREE.MeshStandardMaterial({ color: 0x111111 }));
  wheel.rotation.y = Math.PI / 2; wheel.position.set(0, 0.18, 0.45); barrow.add(wheel);
  const load = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.2, 0.55), new THREE.MeshStandardMaterial({ color: 0xb9b5a8 }));
  load.position.set(0, 0.62, 0); barrow.add(load);
  const bucket = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.11, 0.28, 12), new THREE.MeshStandardMaterial({ color: 0x1d1d1d }));
  const tablet = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.02, 0.18), new THREE.MeshStandardMaterial({ color: 0x0b0b0b, emissive: 0x16304f }));
  const scaffold = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.06, 0.8), new THREE.MeshStandardMaterial({ color: 0x8a6a3e }));
  [carried, barrow, bucket, tablet, scaffold].forEach((o) => { o.castShadow = true; o.visible = false; scene.add(o); });
  scene.add(g);
  const div = document.createElement('div'); div.className = 'label3d';
  const lab = new CSS2DObject(div); scene.add(lab);
  const trailGeo = new THREE.BufferGeometry(); const trailPts = [];
  const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 }));
  trailLayer.add(trail);
  const w = { badge, g, mat, vest, joints, bones, head, helmet, carried, barrow, bucket, tablet, scaffold, div, lab, trail, trailPts, role: '?', last: null };
  workers.set(badge, w);
  return w;
}

const A = new THREE.Vector3(), B = new THREE.Vector3(), D = new THREE.Vector3();
function placeBone(mesh, a, b) {
  D.subVectors(b, a); const len = D.length();
  mesh.position.copy(a);
  mesh.scale.set(1, Math.max(len, 1e-3), 1);
  mesh.quaternion.setFromUnitVectors(UP, D.normalize());
}
const J = (pose, i, v) => v.set(pose[i * 3], pose[i * 3 + 1], pose[i * 3 + 2]);

function updateWorker(w, s) {
  const color = ROLE_COLORS[s.role] ?? ROLE_COLORS['?'];
  if (w.role !== s.role) { w.role = s.role; w.mat.color.setHex(color); w.helmet.material.color.setHex(s.role === 'chef' ? 0xffffff : 0xf5b301); }
  const op = s.stale ? 0.35 : 1;
  w.mat.opacity = op; w.vest.opacity = op; w.head.material.opacity = op;
  const pose = s.pose;
  if (!pose) return;
  for (let i = 0; i < 15; i++) J(pose, i, w.joints[i].position);
  BONES.forEach(([a, b], k) => placeBone(w.bones[k], J(pose, a, A), J(pose, b, B)));
  J(pose, 0, w.head.position);
  w.helmet.position.copy(w.head.position).add(new THREE.Vector3(0, 0.03, 0));
  const hx = Math.sin(s.heading), hz = Math.cos(s.heading);
  const objs = new Set([s.contacts?.l, s.contacts?.r].filter(Boolean));
  const lw = J(pose, 6, A.clone()), rw = J(pose, 7, B.clone());
  const mid = lw.clone().add(rw).multiplyScalar(0.5);
  w.carried.visible = objs.has('parpaing') && !objs.has('brouette');
  if (w.carried.visible) { w.carried.position.copy(mid).add(new THREE.Vector3(0, -0.08, 0)); w.carried.rotation.y = s.heading + Math.PI / 2; }
  w.barrow.visible = objs.has('brouette') || ['chargement'].includes(s.action) && w.role === 'manoeuvre';
  if (w.barrow.visible) { w.barrow.position.set(s.x + hx * 0.95, 0, s.z + hz * 0.95); w.barrow.rotation.y = s.heading; }
  w.bucket.visible = objs.has('seau');
  if (w.bucket.visible) w.bucket.position.copy(rw).add(new THREE.Vector3(0, -0.16, 0));
  w.tablet.visible = objs.has('tablette');
  if (w.tablet.visible) { w.tablet.position.copy(mid); w.tablet.rotation.y = s.heading; w.tablet.rotation.x = -0.5; }
  w.scaffold.visible = s.elev > 0.05;
  if (w.scaffold.visible) { w.scaffold.position.set(s.x, s.elev - 0.03, s.z); w.scaffold.rotation.y = s.heading; }
  w.lab.position.set(pose[0], pose[1] + 0.45, pose[2]);
  w.div.style.borderColor = ROLE_CSS[s.role] ?? '#999';
  const txt = `${s.badge}<small>${site.actions[s.action] ?? s.action}</small>`;
  if (w.div._t !== txt) { w.div.innerHTML = txt; w.div._t = txt; }
  w.last = s;
}

// ------------------------------------------------------------------ interpolation temporelle
const frames = [];            // états triés par t (sim)
let displayT = 0, liveT = 0, rate = 10, lastArrival = 0, lastArrivalT = 0;
let mode = 'live', playing = true, replaySpeed = 10, replayLoading = false, replayLoadedTo = 0;
let lastFrameIdx = 0;

function pushFrame(state) {
  if (frames.length && state.t <= frames[frames.length - 1].t) return;
  frames.push(state);
  if (frames.length > 4000) frames.splice(0, frames.length - 3000);
}
function lerpAngle(a, b, u) { let d = ((b - a + Math.PI) % (2 * Math.PI)) - Math.PI; if (d < -Math.PI) d += 2 * Math.PI; return a + d * u; }
function sampleAt(t) {
  if (!frames.length) return null;
  let i = Math.min(lastFrameIdx, frames.length - 1);
  while (i > 0 && frames[i].t > t) i--;
  while (i < frames.length - 1 && frames[i + 1].t <= t) i++;
  lastFrameIdx = i;
  const a = frames[i], b = frames[Math.min(i + 1, frames.length - 1)];
  if (a === b || b.t <= a.t || t <= a.t) return a;
  const u = Math.min(1, (t - a.t) / (b.t - a.t));
  const bm = new Map(b.workers.map((w) => [w.badge, w]));
  return {
    t, workers: a.workers.map((wa) => {
      const wb = bm.get(wa.badge);
      if (!wb || !wa.pose || !wb.pose) return wa;
      return { ...(u < 0.5 ? wa : wb), x: wa.x + (wb.x - wa.x) * u, z: wa.z + (wb.z - wa.z) * u,
        heading: lerpAngle(wa.heading, wb.heading, u), pose: wa.pose.map((v, k) => v + (wb.pose[k] - v) * u) };
    }),
  };
}

// ------------------------------------------------------------------ réseau
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'state') onState(msg.data);
    else if (msg.type === 'event') onEvent(msg.data);
    else if (msg.type === 'metrics') onMetrics(msg.data);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}
function onState(s) {
  const now = performance.now();
  if (lastArrival) {
    const r = (s.t - lastArrivalT) / Math.max((now - lastArrival) / 1000, 1e-3);
    if (r > 0 && r < 200) rate += (r - rate) * 0.1;
  }
  lastArrival = now; lastArrivalT = s.t;
  liveT = s.t;
  if (mode === 'live') pushFrame(s);
}
const events = [];
function onEvent(e) {
  if (e.type === 'block_placed') { blocks.push({ slot: e.slot, t: e.t, badge: e.badge }); }
  events.unshift(e); if (events.length > 80) events.pop();
  renderEvents();
}
let lastMetrics = null;
function onMetrics(m) { lastMetrics = m; renderPipeline(m); renderProgress(m.stream?.progress); }

async function init() {
  const snap = await fetch('/api/snapshot').then((r) => r.json());
  blocks = snap.blocks.sort((a, b) => a.t - b.t);
  snap.events.reverse().forEach((e) => events.push(e));
  renderEvents();
  if (snap.state) { pushFrame(snap.state); liveT = snap.state.t; displayT = liveT - 0.5; }
  if (snap.pipeline) onMetrics(snap.pipeline);
  connect();
}

// ------------------------------------------------------------------ panneaux UI
const fmtClock = (t) => {
  const d = new Date(START.getTime() + t * 1000);
  return d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }) + ' ' +
    d.toLocaleTimeString('fr-FR', { timeZone: 'UTC' });
};
const fmtDur = (t) => { t = Math.max(0, Math.round(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60; return (h ? h + 'h' : '') + String(m).padStart(h ? 2 : 1, '0') + 'm' + String(s).padStart(2, '0') + 's'; };
const fmtN = (n) => (n ?? 0).toLocaleString('fr-FR');
$('#site-id').textContent = site.site_id;

const WALL_NAMES = { S: 'Sud', E: 'Est', N: 'Nord', W: 'Ouest' };
function renderProgress(p) {
  if (!p) return;
  const shownCount = mode === 'live' ? p.built : shown.reduce((a, b) => a + b, 0);
  const pct = shownCount / p.total * 100;
  $('#pct').textContent = pct.toFixed(1).replace('.', ',');
  $('#pct-bar').style.width = pct + '%';
  $('#built').textContent = `${fmtN(shownCount)} / ${fmtN(p.total)}`;
  const live = mode === 'live';
  $('#rate').textContent = live && p.blocks_per_hour ? `${fmtN(Math.round(p.blocks_per_hour))} blocs/h` : '—';
  $('#eta').textContent = !live ? '—' : p.built >= p.total ? 'Terminé' : p.eta_t ? fmtClock(p.eta_t).split(' ').pop() : '—';
  const walls = {};
  for (const [k, v] of Object.entries(p.walls)) walls[k] = { built: live ? v.built : 0, total: v.total };
  if (!live) slots.forEach((sl, i) => { if (shown[i]) walls[sl.wall].built++; });
  const html = Object.entries(walls).map(([k, v]) =>
    `<span>${WALL_NAMES[k]}</span><div class="mini"><i style="width:${v.built / v.total * 100}%"></i></div><span>${v.built}/${v.total}</span>`).join('');
  if ($('#walls')._h !== html) { $('#walls').innerHTML = html; $('#walls')._h = html; }
}

let selected = null, view = 'orbit';
let lastPanelUpdate = 0;
function renderWorkers(state, now) {
  if (now - lastPanelUpdate < 250 || !state) return;
  lastPanelUpdate = now;
  const roles = lastMetrics?.stream?.roles || {};
  $('#workers').innerHTML = state.workers.map((w) => {
    const [role, conf] = roles[w.badge] || [w.role, 0];
    return `<div class="worker ${selected === w.badge ? 'sel' : ''} ${w.stale ? 'stale' : ''}" data-badge="${w.badge}">
      <span class="dot" style="background:${ROLE_CSS[role] ?? '#999'}"></span>
      <span class="name">${w.badge}</span>
      <span class="role">${site.roles[role] ?? role}${conf ? ` · ${Math.round(conf * 100)}%` : ''}</span>
      <span class="act"><b>${site.actions[w.action] ?? w.action}</b><span>${w.sources == null ? '' : w.stale ? '0 capteur' : `${w.sources} capteur${w.sources > 1 ? 's' : ''}`}</span></span>
    </div>`;
  }).join('');
}
$('#workers').addEventListener('click', (e) => {
  const el = e.target.closest('.worker'); if (!el) return;
  selected = selected === el.dataset.badge ? null : el.dataset.badge;
  if (selected && view === 'orbit') setView('follow');
  if (!selected) setView('orbit');
  lastPanelUpdate = 0;
});

function renderPipeline(m) {
  const r = m.bus_stats?.rate || {}, br = m.bus_stats?.bytes_rate || {};
  const f = m.stream?.fusion || {}, ev = m.stream?.evaluation || {}, lake = m.stream?.lake || {}, edge = m.edge || {};
  const kb = (t) => ((br[t] || 0) / 1024).toFixed(0);
  const row = (k, v, cls = '') => `<div class="row ${cls}"><span>${k}</span><b>${v}</b></div>`;
  const totalIn = (r['edge.pov.frames'] || 0) + (r['edge.fixedcam.detections'] || 0) + (r['edge.pov.interactions'] || 0);
  $('#bus-badge').textContent = m.bus === 'kafka' ? 'Apache Kafka' : 'Bus mémoire';
  $('#bus-kind').textContent = `${fmtN(Math.round(totalIn))} msg/s`;
  const outages = Object.keys(edge.outage || {}).length;
  $('#pipeline').innerHTML = `
    <div class="stage edge"><h3>Edge · capteurs <span>x${edge.speed ?? '—'}</span></h3>
      ${row('4 caméras fixes', `${fmtN(Math.round(r['edge.fixedcam.detections'] || 0))}/s · ${kb('edge.fixedcam.detections')} Ko/s`)}
      ${row('5 body-cams POV', `${fmtN(Math.round(r['edge.pov.frames'] || 0))}/s · ${kb('edge.pov.frames')} Ko/s`)}
      ${row('Coupures wifi en cours', outages ? `${outages} (buffer SD)` : '0', outages ? 'warn' : '')}</div>
    <div class="stage bus"><h3>Bus asynchrone <span>${m.bus}</span></h3>
      ${row('Messages publiés', fmtN(Object.values(m.bus_stats?.total || {}).reduce((a, b) => a + b, 0)))}
      ${row('Volume', ((Object.values(m.bus_stats?.bytes_total || {}).reduce((a, b) => a + b, 0)) / 1e6).toFixed(1) + ' Mo')}</div>
    <div class="stage stream"><h3>Stream · fusion event-time</h3>
      ${row('Fenêtre / retard toléré', `${f.window ?? '—'} s / ${f.allowed_lateness ?? '—'} s`)}
      ${row('Watermark', f.watermark != null ? fmtClock(f.watermark).split(' ').pop() : '—')}
      ${row('Arrivés dans le désordre', fmtN(f.out_of_order))}
      ${row('Trop tardifs (→ batch)', fmtN(f.late), f.late ? 'warn' : '')}
      ${row('Précision actions', ev.action_accuracy != null ? (ev.action_accuracy * 100).toFixed(1) + ' %' : '—')}
      ${row('Erreur position', ev.position_mae_m != null ? (ev.position_mae_m * 100).toFixed(0) + ' cm' : '—')}</div>
    <div class="stage lake"><h3>Data lake · Parquet</h3>
      ${row('Fichiers / lignes', `${fmtN(lake.files)} / ${fmtN(lake.rows)}`)}
      ${row('Taille compressée', `${lake.mb ?? 0} Mo`)}
      ${row('Alertes TMS', fmtN(m.stream?.alerts))}</div>`;
}

const EV_FMT = {
  block_placed: (e) => ['block', 'Pose', `${e.badge} · mur ${WALL_NAMES[e.wall]} rang ${e.row + 1} <small>(±${e.error_cm} cm)</small>`],
  posture_risque: (e) => ['alert', 'TMS', `${e.badge} · dos fléchi en charge`],
  anomalie_pose: (e) => ['alert', 'BIM', `${e.badge} · pose hors plan`],
  barrow_unloaded: (e) => ['supply', 'Appro', `${e.badge} → ${e.to} · ${e.count} parpaings`],
  mortar_delivered: (e) => ['supply', 'Mortier', `${e.badge} → ${e.to}`],
  segment_done: (e) => ['info', 'Fini', `${e.badge} a terminé ses murs`],
};
let evPending = false;
function renderEvents() {
  if (evPending) return; evPending = true;
  requestAnimationFrame(() => {
    evPending = false;
    $('#events').innerHTML = events.slice(0, 40).map((e) => {
      const f = EV_FMT[e.type]; if (!f) return '';
      const [cls, tag, txt] = f(e);
      return `<div class="ev"><time>${fmtClock(e.t).split(' ').pop()}</time><span><span class="tag ${cls}">${tag}</span>${txt}</span></div>`;
    }).join('');
  });
}

// ------------------------------------------------------------------ vues caméra
function setView(v) {
  view = v;
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
  if ((v === 'follow' || v === 'pov') && !selected) selected = [...workers.keys()][0];
  controls.enabled = v !== 'pov';
  if (v === 'orbit') { controls.target.set(0, 0.5, 0); camera.position.set(-11, 11, -15); }
  if (v === 'follow' && selected && workers.get(selected)?.last) { const l = workers.get(selected).last; camera.position.set(l.x - 5, 5, l.z - 6); }
  $('#pov-banner').hidden = v !== 'pov';
  workers.forEach((w) => { w.lab.visible = true; });
}
document.querySelectorAll('#views button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

const layers = { cams: camLayer, trails: trailLayer };
let heatLayer = null;
document.querySelectorAll('#layers button').forEach((b) => b.addEventListener('click', async () => {
  const on = !b.classList.contains('on'); b.classList.toggle('on', on);
  const k = b.dataset.layer;
  if (k === 'bim') ghost.visible = on;
  else if (k === 'heat') { if (on) await buildHeatmap(); if (heatLayer) heatLayer.visible = on; }
  else layers[k].visible = on;
}));

async function buildHeatmap() {
  const a = await fetch('/api/analytics').then((r) => r.json());
  if (heatLayer) scene.remove(heatLayer);
  heatLayer = new THREE.Group();
  const max = Math.max(1, ...a.heatmap.map((c) => c.n));
  const ramp = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'].reverse();
  for (const c of a.heatmap) {
    const v = Math.log1p(c.n) / Math.log1p(max);
    const color = new THREE.Color(ramp[ramp.length - 1 - Math.min(ramp.length - 1, Math.floor(v * ramp.length))]);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.96, 0.96), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25 + 0.6 * v, depthWrite: false }));
    m.rotation.x = -Math.PI / 2; m.position.set(c.gx + 0.5, 0.02, c.gz + 0.5);
    heatLayer.add(m);
  }
  scene.add(heatLayer);
}

// ------------------------------------------------------------------ live / replay
const tl = $('#timeline');
function setMode(m) {
  mode = m;
  $('#mode-badge').className = 'badge ' + (m === 'live' ? 'live' : 'replay');
  $('#mode-badge').textContent = m === 'live' ? '● LIVE' : '◀ REPLAY';
  $('#btn-live').classList.toggle('accent', m !== 'live');
  playing = true; $('#btn-play').textContent = '❚❚';
  workers.forEach((w) => { w.trailPts.length = 0; });
}
async function startReplay(t) {
  setMode('replay');
  frames.length = 0; lastFrameIdx = 0;
  displayT = t; replayLoadedTo = t;
  await loadReplay();
}
async function loadReplay() {
  if (replayLoading || replayLoadedTo >= liveT - 3) return;
  replayLoading = true;
  try {
    const t0 = replayLoadedTo, t1 = Math.min(liveT - 3, t0 + 60);
    const data = await fetch(`/api/replay?t0=${t0}&t1=${t1}`).then((r) => r.json());
    if (mode === 'replay') { data.frames.forEach(pushFrame); replayLoadedTo = t1; }
  } finally { replayLoading = false; }
}
tl.addEventListener('input', () => { displayT = +tl.value; $('#tl-cur').textContent = fmtClock(displayT); });
tl.addEventListener('change', () => startReplay(+tl.value));
$('#btn-live').addEventListener('click', () => { setMode('live'); frames.length = 0; lastFrameIdx = 0; displayT = liveT - 0.5; });
$('#btn-play').addEventListener('click', () => {
  if (mode === 'live') {
    playing = !playing;
    fetch('/api/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cmd: playing ? 'resume' : 'pause' }) });
  } else playing = !playing;
  $('#btn-play').textContent = playing ? '❚❚' : '▶';
});
document.querySelectorAll('#speeds button').forEach((b) => b.addEventListener('click', () => {
  const v = +b.dataset.speed;
  document.querySelectorAll('#speeds button').forEach((x) => x.classList.toggle('on', x === b));
  if (mode === 'live') fetch('/api/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cmd: 'speed', value: v }) });
  else replaySpeed = v;
}));

// ------------------------------------------------------------------ analytics & export
const CATS = [
  ['Production', ['prise_parpaing', 'mortier', 'pose_parpaing', 'reglage'], 'var(--cat-1)'],
  ['Logistique', ['transport_brouette', 'transport_seau', 'chargement', 'dechargement', 'gachage'], 'var(--cat-2)'],
  ['Déplacement', ['marche'], 'var(--cat-3)'],
  ['Inspection', ['inspection'], 'var(--cat-4)'],
  ['Attente', ['attente'], 'var(--cat-5)'],
];
const tip = $('#tooltip');
function bindTips(root) {
  root.querySelectorAll('[data-tip]').forEach((el) => {
    el.addEventListener('mousemove', (e) => { tip.hidden = false; tip.innerHTML = el.dataset.tip; tip.style.left = e.clientX + 12 + 'px'; tip.style.top = e.clientY + 12 + 'px'; });
    el.addEventListener('mouseleave', () => { tip.hidden = true; });
  });
}
function openModal(html) { $('#modal-body').innerHTML = html; $('#modal').hidden = false; bindTips($('#modal-body')); }
$('#modal-close').addEventListener('click', () => { $('#modal').hidden = true; tip.hidden = true; });
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') { $('#modal').hidden = true; tip.hidden = true; } });

$('#btn-analytics').addEventListener('click', async () => {
  openModal('<h1>Analytics chantier</h1><p>Requêtes DuckDB sur la couche silver du data lake…</p>');
  const a = await fetch('/api/analytics').then((r) => r.json());
  const p = lastMetrics?.stream?.progress || {};
  const blocksBy = Object.fromEntries(a.blocks_by_worker.map((r) => [r.badge, r.blocks]));
  const cycle = Object.fromEntries(a.cycle_time.map((r) => [r.badge, r.median_cycle_s]));
  const alerts = a.alerts.filter((r) => r.type === 'posture_risque').reduce((s, r) => s + r.n, 0);
  const totalDist = a.workers.reduce((s, r) => s + r.distance_m, 0);
  // barres empilées : part du temps par catégorie et par ouvrier
  const per = {};
  a.time_by_action.forEach((r) => { per[r.badge] ??= {}; per[r.badge][r.action] = r.seconds; });
  const badges = Object.keys(per).sort();
  const W = 900, rowH = 30, left = 150, barW = W - left - 20;
  let bars = '';
  badges.forEach((b, i) => {
    const tot = Object.values(per[b]).reduce((s, v) => s + v, 0) || 1;
    let x = left;
    CATS.forEach(([name, acts, color]) => {
      const v = acts.reduce((s, k) => s + (per[b][k] || 0), 0);
      const w = v / tot * barW;
      if (w > 0.5) {
        const detail = acts.filter((k) => per[b][k]).map((k) => `${site.actions[k]} : ${fmtDur(per[b][k])}`).join('<br>');
        bars += `<rect x="${x}" y="${i * rowH + 6}" width="${Math.max(0, w - 2)}" height="${rowH - 12}" rx="3" fill="${color}" data-tip="<b>${b} · ${name}</b> — ${(v / tot * 100).toFixed(0)} %<br>${detail}"/>`;
      }
      x += w;
    });
    const wr = a.workers.find((r) => r.badge === b);
    bars += `<text x="0" y="${i * rowH + rowH / 2 + 4}">${b} · ${site.roles[wr?.role] ?? ''}</text>`;
  });
  const legend = CATS.map(([n, , c]) => `<span><i style="background:${c}"></i>${n}</span>`).join('');
  // courbe d'avancement cumulée
  const pc = a.progress_curve, total = p.total || 518, B = a.bucket_s || 60;
  const cw = 900, ch = 200, maxT = Math.max(B, ...pc.map((r) => r.t + B));
  const px = (t) => 40 + t / maxT * (cw - 60), py = (v) => ch - 30 - v / total * (ch - 46);
  const path = pc.map((r, i) => `${i ? 'L' : `M${px(0)},${py(0)}L`}${px(r.t + B).toFixed(1)},${py(r.cumulative).toFixed(1)}`).join('');
  const pts = pc.length > 60 ? '' : pc.map((r) => `<circle cx="${px(r.t + B)}" cy="${py(r.cumulative)}" r="4" fill="var(--cat-1)" stroke="var(--panel-solid)" stroke-width="2" data-tip="${fmtClock(r.t + B).split(' ').pop()} — ${r.cumulative} parpaings (+${r.blocks})"/>`).join('');
  const ticksX = [0, 0.25, 0.5, 0.75, 1].map((f) => `<text x="${px(f * maxT)}" y="${ch - 8}" text-anchor="middle">${fmtClock(f * maxT).split(' ').pop().slice(0, 5)}</text>`).join('');
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => `<line class="grid" x1="40" x2="${cw - 20}" y1="${py(f * total)}" y2="${py(f * total)}"/><text x="0" y="${py(f * total) + 4}">${Math.round(f * 100)}%</text>`).join('');
  openModal(`<h1>Analytics chantier</h1><p>Calculé à la demande par DuckDB directement sur les fichiers Parquet de la couche silver (aucune base de données à maintenir).</p>
    <div class="tiles">
      <div class="tile"><div class="v">${fmtN(p.built)}</div><div class="l">parpaings posés (${((p.progress || 0) * 100).toFixed(1)} %)</div></div>
      <div class="tile"><div class="v">${Object.values(cycle).length ? Math.min(...Object.values(cycle)).toFixed(1) + ' s' : '—'}</div><div class="l">cycle médian de pose (meilleur maçon)</div></div>
      <div class="tile"><div class="v">${fmtN(Math.round(totalDist))} m</div><div class="l">parcourus par l'équipe</div></div>
      <div class="tile"><div class="v">${alerts}</div><div class="l">alertes posture (TMS)</div></div>
    </div>
    <h3>Répartition du temps par ouvrier</h3><div class="legend">${legend}</div>
    <svg viewBox="0 0 ${W} ${badges.length * rowH}" width="100%">${bars}</svg>
    <h3>Courbe d'avancement (parpaings cumulés)</h3>
    <svg viewBox="0 0 ${cw} ${ch}" width="100%">${gridY}${ticksX}<path d="${path}" fill="none" stroke="var(--cat-1)" stroke-width="2"/>${pts}</svg>
    <h3>Tableau ouvriers</h3>
    <table class="data"><tr><th>Badge</th><th>Rôle déduit</th><th class="num">Parpaings</th><th class="num">Cycle médian</th><th class="num">Distance</th><th class="num">Attente</th></tr>
    ${a.workers.map((r) => `<tr><td>${r.badge}</td><td>${site.roles[r.role] ?? r.role}</td><td class="num">${blocksBy[r.badge] ?? '—'}</td><td class="num">${cycle[r.badge] ? cycle[r.badge] + ' s' : '—'}</td><td class="num">${fmtN(r.distance_m)} m</td><td class="num">${r.idle_pct} %</td></tr>`).join('')}
    </table>`);
});

$('#btn-export').addEventListener('click', async () => {
  openModal('<h1>Dataset robotique</h1><p>Segmentation des trajectoires en épisodes…</p>');
  const s = await fetch('/api/export/summary').then((r) => r.json());
  const rows = Object.entries(s.by_task).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${site.actions[k] ?? k}</td><td><code>${k}</code></td><td class="num">${fmtN(v)}</td></tr>`).join('');
  openModal(`<h1>Dataset robotique</h1>
    <p>Chaque geste reconnu devient un <b>épisode de démonstration</b> : la pose 3D de l'ouvrier frame par frame, sa position sur le chantier, la cible BIM et la commande suivante. Format compatible <b>LeRobot</b> (Hugging Face), le standard de l'apprentissage par imitation.</p>
    <div class="tiles">
      <div class="tile"><div class="v">${fmtN(s.episodes)}</div><div class="l">épisodes</div></div>
      <div class="tile"><div class="v">${fmtN(s.frames)}</div><div class="l">frames (${(1 / site.window).toFixed(0)} Hz)</div></div>
      <div class="tile"><div class="v">45</div><div class="l">dimensions d'état (15 articulations × 3)</div></div>
      <div class="tile"><div class="v">0</div><div class="l">visage / nom exporté (RGPD)</div></div>
    </div>
    <h3>Épisodes par tâche</h3>
    <table class="data"><tr><th>Tâche</th><th>Identifiant</th><th class="num">Épisodes</th></tr>${rows}</table>
    <h3>Structure de l'archive</h3>
    <pre class="code">meta/info.json         schéma, fps, repères, noms des articulations
meta/tasks.jsonl       « Poser un parpaing à son emplacement… »
meta/episodes.jsonl    {"episode_index": 12, "task": "pose_parpaing", "bim_slot": "S-R3-04", …}
meta/stats.json        moyennes / écarts-types (normalisation)
data/chunk-000/episode_000012.parquet
   observation.state[45]  observation.base[4]  observation.target[4]  action[45]  next.done</pre>
    <p style="margin-top:14px"><a href="/api/export/dataset.zip" download><button class="accent">Télécharger le dataset (.zip)</button></a></p>`);
});

// ------------------------------------------------------------------ boucle de rendu
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); labelRenderer.setSize(innerWidth, innerHeight);
});
let lastNow = performance.now(), lastTrail = 0;
const camPos = new THREE.Vector3(), camLook = new THREE.Vector3();
function tick(now) {
  requestAnimationFrame(tick);
  const dt = Math.min(0.1, (now - lastNow) / 1000); lastNow = now;
  if (mode === 'live') {
    const target = liveT - Math.max(0.3, rate * 0.12);
    if (playing) displayT += dt * rate;
    displayT += (target - displayT) * Math.min(1, dt * 2);
    if (Math.abs(target - displayT) > 10) displayT = target;
  } else {
    if (playing) displayT += dt * replaySpeed;
    const last = frames.length ? frames[frames.length - 1].t : displayT;
    if (displayT > last) displayT = last;
    if (replayLoadedTo - displayT < 30) loadReplay();
  }
  const st = sampleAt(displayT);
  if (st) {
    for (const s of st.workers) updateWorker(workers.get(s.badge) || makeWorker(s.badge), s);
    renderWorkers(st, now);
    if (trailLayer.visible && now - lastTrail > 200) {
      lastTrail = now;
      for (const s of st.workers) {
        const w = workers.get(s.badge); w.trailPts.push(new THREE.Vector3(s.x, 0.05, s.z));
        if (w.trailPts.length > 400) w.trailPts.shift();
        w.trail.geometry.setFromPoints(w.trailPts); w.trail.material.color.setHex(ROLE_COLORS[s.role] ?? 0xffffff);
      }
    }
  }
  applyBlocks(displayT, now);
  if (mode !== 'live' && lastMetrics?.stream?.progress) renderProgress(lastMetrics.stream.progress);
  // timeline
  tl.max = Math.max(1, liveT).toFixed(1);
  if (document.activeElement !== tl) tl.value = displayT.toFixed(1);
  $('#tl-cur').textContent = fmtClock(Math.max(0, displayT));
  $('#tl-end').textContent = fmtDur(liveT);
  $('#clock').textContent = fmtClock(Math.max(0, displayT));
  // caméra
  const w = selected && workers.get(selected);
  if (w && w.last && view === 'follow') {
    camLook.set(w.last.x, 1, w.last.z);
    controls.target.lerp(camLook, 0.08);
  }
  if (w && w.last && w.last.pose && view === 'pov') {
    const p = w.last.pose, h = w.last.heading;
    camPos.set(p[3] + Math.sin(h) * 0.18, p[4] - 0.05, p[5] + Math.cos(h) * 0.18);   // sur le torse
    camera.position.lerp(camPos, 0.5);
    const lw = new THREE.Vector3(p[18], p[19], p[20]), rw = new THREE.Vector3(p[21], p[22], p[23]);
    camLook.copy(lw.add(rw).multiplyScalar(0.5)).lerp(new THREE.Vector3(camPos.x + Math.sin(h) * 2, camPos.y - 0.6, camPos.z + Math.cos(h) * 2), 0.3);
    camera.lookAt(camLook);
    w.g.visible = true; w.head.visible = false; w.helmet.visible = false; w.lab.visible = false;
    $('#pov-banner').textContent = `● REC · Body-cam ${w.badge} — reconstruction 3D`;
  } else {
    workers.forEach((x) => { x.head.visible = true; x.helmet.visible = true; });
    controls.update();
  }
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}
init();
requestAnimationFrame(tick);
document.querySelector('#speeds button[data-speed="10"]').classList.add('on');
