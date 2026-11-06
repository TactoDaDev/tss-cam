// TSS Cam receiver window: polls /api/status twice a second and redraws only what changed.
'use strict';
const $ = (id) => document.getElementById(id);
const GAME_TITLES = { beamng: 'BeamNG.drive', assetto: 'Assetto Corsa' };
let last = null;
let ambientFor = null;

function setText(id, text) { const el = $(id); if (el && el.textContent !== text) el.textContent = text; }
function pill(el, cls, text) {
  if (!el) return;
  el.className = el.className.replace(/\b(ok|warn|bad)\b/g, '').trim() + (cls ? ' ' + cls : '');
  el.textContent = text || '';
}
async function post(path, body) {
  try { await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); }
  catch (e) { /* the next poll shows the state */ }
  poll();
}

// The ambient layer: the active game's art drawn into a 64x40 canvas, which CSS stretches (a free blur).
function ambient(game) {
  if (game === ambientFor) return;
  ambientFor = game;
  const c = $('ambient'), g = c.getContext('2d');
  g.fillStyle = '#0b0b10'; g.fillRect(0, 0, c.width, c.height);
  if (!game) return;
  const img = new Image();
  img.onload = () => { if (ambientFor === game) { g.drawImage(img, 0, 0, c.width, c.height); g.fillStyle = 'rgba(8,8,12,0.45)'; g.fillRect(0, 0, c.width, c.height); } };
  img.src = `/art/${game}.jpg`;
}

function render(s) {
  const ph = s.phone, gm = s.game;
  // receiver
  const rxOk = !!s.listening && !s.listen_error;
  $('rxDot').className = 'dot ' + (rxOk ? 'ok' : 'bad');
  setText('rxLabel', rxOk ? 'RECEIVER LISTENING' : 'RECEIVER ERROR');
  setText('rxLine', rxOk ? `UDP ${s.port} · v${s.version}` : (s.listen_error || 'not listening'));
  // phone hero
  const live = ph.live;
  pill($('phonePill'), live ? 'ok' : 'warn', '');
  $('phonePill').innerHTML = `<span class="dot"></span><span>${live ? 'PHONE LIVE' : 'WAITING FOR THE PHONE'}</span>`;
  setText('heroTitle', live ? (ph.frozen ? 'Frozen' : ph.walk ? 'Walking' : 'Filming') : 'Open TSS Cam on your phone');
  setText('heroSub', live
    ? `${ph.source} · ${ph.rate} Hz · ${ph.latency == null ? '-' : ph.latency + ' ms'} · ${ph.tracking}${ph.app ? ' · app ' + ph.app : ''}`
    : `Enter ${s.ips[0] || 'this PC\'s address'} in the app. Phone and PC on the same Wi-Fi.`);
  $('freeze').classList.toggle('on', !!ph.frozen);
  // game tiles
  for (const t of document.querySelectorAll('.tile')) {
    t.classList.toggle('active', t.dataset.mode === gm.mode);
    const st = t.querySelector('[data-state]');
    if (st) {
      const isActive = gm.game === t.dataset.mode;
      st.textContent = isActive ? (gm.game_connected ? 'Connected' : 'Waiting for the game') : (gm.mode === t.dataset.mode ? 'Selected' : '');
    }
  }
  setText('gameHint', gm.mode === 'auto' ? (gm.game ? `Auto: ${GAME_TITLES[gm.game]} is running` : 'Auto picks whichever game is running') : 'Fixed game');
  ambient(gm.game);
  // connect
  setText('addr', s.ips.length ? s.ips.join('  ·  ') : 'No network');
  setText('port', String(s.port));
  pill($('fwPill'), s.firewall ? 'ok' : 'warn', s.firewall ? 'FIREWALL OK' : 'FIREWALL?');
  $('firewall').classList.toggle('hidden', !!s.firewall || live);
  // car
  pill($('gamePill'), gm.game_connected ? 'ok' : (gm.game ? 'warn' : ''), gm.game ? (gm.game_connected ? 'LINKED' : 'NOT LINKED') : '');
  setText('car', gm.car || (gm.game ? GAME_TITLES[gm.game] : 'No game running'));
  setText('carLine', gm.hook_error || (gm.car ? `${gm.controls} controls on the phone` : (gm.game ? 'Start the game with the TSS Cam mod' : 'Start BeamNG.drive or Assetto Corsa')));
  // live view
  const lv = s.live;
  pill($('livePill'), lv.error ? 'bad' : (lv.running ? 'ok' : ''), lv.error ? 'ERROR' : (lv.running ? 'ON' : ''));
  setText('liveBig', lv.running ? `${lv.fps} fps · ${lv.mbps} Mbit/s` : 'Off');
  setText('liveLine', lv.error || (lv.running ? `encoder ${lv.encoder || '-'}` : 'Starts when the phone\'s viewfinder shows the game'));
  // settings
  setText('ver', 'v' + s.version);
  setText('sLoss', ph.loss + ' %');
  setText('sTrack', ph.tracking || '-');
  setText('sZoom', ph.zoom + 'x');
  setText('sCounts', `${gm.counts.command} / ${gm.counts.sticks} / ${gm.counts.bad}`);
  setText('sListen', s.listening || s.listen_error || '-');
}

async function poll() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    last = await r.json();
    render(last);
  } catch (e) {
    setText('rxLabel', 'RECEIVER STOPPED');
    $('rxDot').className = 'dot bad';
  }
}

for (const t of document.querySelectorAll('.tile')) t.addEventListener('click', () => post('/api/mode', { mode: t.dataset.mode }));
$('recenter').addEventListener('click', () => post('/api/recenter'));
$('freeze').addEventListener('click', () => post('/api/freeze'));
$('accept').addEventListener('click', () => post('/api/accept_phone'));
$('firewall').addEventListener('click', () => post('/api/firewall'));
for (const n of document.querySelectorAll('.nav')) n.addEventListener('click', () => {
  for (const m of document.querySelectorAll('.nav')) m.classList.toggle('active', m === n);
  for (const v of document.querySelectorAll('.view')) v.classList.toggle('hidden', v.id !== n.dataset.view);
});
poll();
setInterval(poll, 500);
