// Animica Internet popup: live .anm search (Google-like) + dVPN browser-proxy location picker.
const GATEWAY = 'https://animica.dev';
const API = GATEWAY + '/api/mkt/v1/names';
const VPN_API = GATEWAY + '/api/mkt/v1/vpn/locations';

const $ = (id) => document.getElementById(id);
function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
function descOf(r) { try { return JSON.parse(r.recordsJson).description || r.kind; } catch { return r.kind; } }
function send(msg) { return new Promise((res) => chrome.runtime.sendMessage(msg, res)); }

/* ---------------------------------------------------------------- tabs */
document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    const which = t.dataset.tab;
    $('pane-names').classList.toggle('active', which === 'names');
    $('pane-vpn').classList.toggle('active', which === 'vpn');
    if (which === 'vpn') loadExits();
    else $('q').focus();
  });
});

/* ------------------------------------------------------------- names */
const q = $('q'), results = $('results'), stat = $('stat');
let timer = null;
async function runNames(query) {
  try {
    const r = await fetch(`${API}?search=${encodeURIComponent(query)}`);
    const d = await r.json();
    if (typeof d.total === 'number') stat.textContent = `${d.total} names`;
    const rows = d.results || [];
    if (!rows.length) {
      results.innerHTML = query
        ? `<div class="empty">No .anm name matches “${esc(query)}”.<br><a href="${GATEWAY}/names" target="_blank" style="color:#7cc4ff">Register it →</a></div>`
        : '<div class="empty">Type to search the sovereign .anm index.</div>';
      return;
    }
    results.innerHTML = rows.map((x) => `
      <a class="row" href="${GATEWAY}/anm/${esc(x.name)}" target="_blank">
        <div><span class="name">${esc(x.name)}.anm</span><span class="kind">${esc(x.kind)}</span></div>
        <div class="desc">${esc(descOf(x))}</div>
      </a>`).join('');
  } catch {
    results.innerHTML = '<div class="empty">Network error reaching the gateway.</div>';
  }
}
q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => runNames(q.value.trim()), 160); });
q.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const v = q.value.trim().replace(/\.anm$/, '');
    if (/^[a-z0-9-]{2,63}$/.test(v)) window.open(`${GATEWAY}/anm/${v}`, '_blank');
    else window.open(`${GATEWAY}/names?search=${encodeURIComponent(v)}`, '_blank');
  }
});
runNames('');

/* --------------------------------------------------------------- vpn */
const exitsEl = $('exits'), statusEl = $('vpnStatus'), countEl = $('exitCount');

function flag(cc) {
  if (!cc || cc.length !== 2 || !/^[a-zA-Z]{2}$/.test(cc)) return '🌐';
  const A = 0x1f1e6;
  return String.fromCodePoint(A + cc.toUpperCase().charCodeAt(0) - 65,
                              A + cc.toUpperCase().charCodeAt(1) - 65);
}
function locLabel(x) {
  return [x.city, x.country && x.country.toUpperCase()].filter(Boolean).join(', ') || x.region || x.label || x.id;
}

async function renderStatus() {
  const { proxy } = await send({ type: 'vpn.get' });
  if (proxy) {
    statusEl.className = 'status on';
    statusEl.innerHTML = `<span class="stop" id="vpnStop">Stop</span>Routed via <b>${esc(proxy.label || proxy.host)}</b> — browser proxy active.`;
    $('vpnStop').addEventListener('click', async () => { await send({ type: 'vpn.clear' }); await refreshVpn(); });
  } else {
    statusEl.className = 'status'; statusEl.innerHTML = '';
  }
  return proxy;
}

async function ensureAuthPerms() {
  const has = await chrome.permissions.contains({
    permissions: ['webRequest', 'webRequestAuthProvider'], origins: ['<all_urls>'],
  });
  if (has) return true;
  return chrome.permissions.request({
    permissions: ['webRequest', 'webRequestAuthProvider'], origins: ['<all_urls>'],
  });
}

async function route(x) {
  const [host, portStr] = String(x.httpProxy).split(':');
  const port = Number(portStr);
  if (!host || !port) { alert('This exit has no browser proxy endpoint.'); return; }
  let token = '';
  if (x.proxyAuth) {
    token = (prompt(`Exit "${locLabel(x)}" requires an access token (get one at animica.dev/vpn):`) || '').trim();
    if (!token) return;
    const ok = await ensureAuthPerms();
    if (!ok) { alert('Permission needed to send the proxy token. Not routed.'); return; }
  }
  const proxy = { exitId: x.id, label: locLabel(x), host, port, token };
  const res = await send({ type: 'vpn.set', proxy });
  if (!res || !res.ok) { alert('Failed to route: ' + (res && res.error || 'unknown')); return; }
  await refreshVpn();
}

async function loadExits() {
  const active = await renderStatus();
  try {
    const r = await fetch(VPN_API);
    const d = await r.json();
    const xs = (d.exits || d.results || []).filter((x) => x.httpProxy);
    countEl.textContent = xs.length ? `${xs.length} online` : '';
    if (!xs.length) {
      exitsEl.innerHTML = '<div class="empty">No browser-proxy exits online yet.<br>'
        + `<a href="${GATEWAY}/vpn" target="_blank" style="color:#7cc4ff">Run one and earn →</a></div>`;
      return;
    }
    xs.sort((a, b) => (a.load ?? 1) - (b.load ?? 1));
    exitsEl.innerHTML = xs.map((x) => {
      const on = active && active.exitId === x.id;
      const meta = [x.region, x.reputation != null ? `rep ${Number(x.reputation).toFixed(1)}` : null,
        x.load != null ? `load ${Math.round(Number(x.load) * 100)}%` : null].filter(Boolean).join(' · ');
      return `<div class="exit" data-id="${esc(x.id)}">
        <div class="flag">${flag(x.country)}</div>
        <div class="loc"><div class="city">${esc(locLabel(x))}${x.proxyAuth ? '<span class="lock">🔒 token</span>' : ''}</div>
          <div class="meta">${esc(meta)}</div></div>
        <button class="btn ${on ? 'on' : ''}">${on ? 'Routed' : 'Route'}</button>
      </div>`;
    }).join('');
    exitsEl.querySelectorAll('.exit').forEach((el) => {
      const x = xs.find((e) => e.id === el.dataset.id);
      el.querySelector('.btn').addEventListener('click', () => {
        if (active && active.exitId === x.id) send({ type: 'vpn.clear' }).then(refreshVpn);
        else route(x);
      });
    });
  } catch {
    exitsEl.innerHTML = '<div class="empty">Could not reach the exit registry.</div>';
  }
}

function refreshVpn() { return loadExits(); }
