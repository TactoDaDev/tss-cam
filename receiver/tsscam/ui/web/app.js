// TSS Cam receiver monitor: polls /api/status twice a second; the timecode runs (at 25 fps) only while the phone
// is live, so an idle window draws nothing.
'use strict';
const $ = (id) => document.getElementById(id);
const GAME = { beamng: 'BEAMNG.DRIVE', assetto: 'ASSETTO CORSA' };
const TITLE = { beamng: 'BeamNG.drive', assetto: 'Assetto Corsa' };
let liveSince = null, tcTimer = null, imageFor;

function setText(id, t) { const e = $(id); if (e && e.textContent !== t) e.textContent = t; }
function cls(el, c, on) { el.classList.toggle(c, !!on); }
async function post(path, body) {
  try { await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); } catch (e) {}
  poll();
}

function timecode() {
  if (liveSince == null) return setText('timecode', '00:00:00:00');
  const t = (performance.now() - liveSince) / 1000, f = Math.floor((t % 1) * 25);
  const p = (n) => String(Math.floor(n)).padStart(2, '0');
  setText('timecode', `${p(t / 3600)}:${p((t / 60) % 60)}:${p(t % 60)}:${p(f)}`);
}

function image(game) {
  if (game === imageFor) return;
  imageFor = game;
  $('image').style.backgroundImage = game ? `url(/art/${game}.jpg)` : 'none';
  // the whole window takes the game's colour: its art in a 64x40 canvas stretched by CSS (a blur that costs nothing)
  const c = $('ambient'), g = c.getContext('2d');
  g.fillStyle = '#0d0e10'; g.fillRect(0, 0, c.width, c.height);
  if (game) {
    const img = new Image();
    img.onload = () => { if (imageFor === game) { g.drawImage(img, 0, 0, c.width, c.height); g.fillStyle = 'rgba(10,10,12,0.5)'; g.fillRect(0, 0, c.width, c.height); } };
    img.src = `/art/${game}.jpg`;
  }
}

function render(s) {
  const ph = s.phone, gm = s.game, lv = s.live;
  // tally + timecode
  const live = ph.live;
  if (live && liveSince == null) liveSince = performance.now();
  if (!live) liveSince = null;
  if (live && !tcTimer) tcTimer = setInterval(timecode, 40);
  if (!live && tcTimer) { clearInterval(tcTimer); tcTimer = null; }
  timecode();
  const tally = $('tally');
  tally.textContent = live ? (ph.frozen ? 'HOLD' : 'LIVE') : (s.listen_error ? 'ERR' : 'STBY');
  cls(tally, 'live', live && !ph.frozen); cls(tally, 'warn', !!s.listen_error || (live && ph.frozen));
  setText('tbMode', gm.mode === 'auto' ? (gm.game ? `AUTO · ${GAME[gm.game]}` : 'AUTO') : GAME[gm.mode]);

  // slate
  const label = $('slateLabel');
  label.textContent = live ? (ph.frozen ? 'HOLD' : ph.walk ? 'WALK' : 'HANDHELD') : 'STANDBY';
  cls(label, 'live', live);
  setText('slateTitle', live ? (gm.car || TITLE[gm.game] || 'Phone live') : (s.listen_error ? 'Receiver error' : 'Waiting for the phone'));
  setText('slateSub', live ? `${ph.source} · APP ${ph.app || '-'} · ${ph.tracking.toUpperCase()}`
                           : (s.listen_error || `ENTER ${s.ips[0] || "THIS PC'S ADDRESS"} IN THE APP · SAME WI-FI`));
  cls($('freeze'), 'on', ph.frozen);
  image(gm.game);
  setText('cornerId', gm.game ? (gm.game_connected ? 'LINK OK' : 'NO LINK') : 'A001');

  // menu: game rows
  for (const r of document.querySelectorAll('.row')) {
    cls(r, 'active', r.dataset.mode === gm.mode);
    const v = r.querySelector('[data-state]');
    if (r.dataset.mode === 'auto') { v.textContent = 'DETECT'; continue; }
    const running = gm.game === r.dataset.mode;
    v.textContent = running ? (gm.game_connected ? 'LINKED' : 'NO LINK') : '';
    v.className = 'val mono' + (running ? (gm.game_connected ? ' ok' : ' warn') : '');
  }
  setText('addr', s.ips.length ? s.ips[0] : 'NO NETWORK');
  setText('port', String(s.port));
  const fw = $('fw');
  fw.textContent = s.firewall ? 'FIREWALL OK' : 'FIREWALL ?';
  cls(fw, 'warn', !s.firewall);
  cls($('firewall'), 'hidden', s.firewall || live);
  setText('car', gm.car || (gm.game ? TITLE[gm.game] : 'No car'));
  setText('carLine', gm.hook_error ? gm.hook_error.toUpperCase()
    : gm.car ? `${gm.controls} CONTROLS ON THE PHONE` : (gm.game ? 'START THE GAME WITH THE TSS CAM MOD' : 'START BEAMNG.DRIVE OR ASSETTO CORSA'));
  setText('liveLine', lv.error ? 'ERROR · ' + lv.error.toUpperCase() : lv.running ? `${lv.fps} FPS · ${lv.mbps} MBIT/S · ${(lv.encoder || '-').toUpperCase()}` : 'OFF');

  // readouts
  setText('rPhone', live ? 'LIVE' : '--');
  setText('rRate', live ? `${ph.rate} HZ` : '-- HZ');
  setText('rLat', ph.latency == null ? '-- MS' : `${ph.latency} MS`);
  const loss = $('rLoss');
  loss.textContent = live ? `${ph.loss} %` : '-- %';
  loss.className = 'v' + (live && ph.loss > 5 ? ' bad' : live && ph.loss > 1 ? ' warn' : '');
  setText('rLens', `${Math.round(24 * (ph.zoom || 1))}MM`);
  setText('rTrack', live ? ph.tracking.toUpperCase() : '--');
  setText('rLink', s.listen_error ? s.listen_error : `UDP ${s.listening || '-'} · ${gm.counts.command} CMD · ${gm.counts.bad} BAD`);
}

async function poll() {
  try { render(await (await fetch('/api/status', { cache: 'no-store' })).json()); }
  catch (e) { const t = $('tally'); t.textContent = 'OFF'; t.className = 'tally warn'; }
}

for (const r of document.querySelectorAll('.row')) r.addEventListener('click', () => post('/api/mode', { mode: r.dataset.mode }));
$('recenter').addEventListener('click', () => post('/api/recenter'));
$('freeze').addEventListener('click', () => post('/api/freeze'));
$('accept').addEventListener('click', () => post('/api/accept_phone'));
$('firewall').addEventListener('click', () => post('/api/firewall'));
poll();
setInterval(poll, 500);
