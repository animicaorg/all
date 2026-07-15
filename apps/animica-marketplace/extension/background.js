// Animica Internet browser — service worker.
//   * "anm" omnibox keyword: search the .anm index like Google, or jump straight to a name.
//     (The *.anm -> gateway auto-redirect is handled declaratively by rules.json.)
//   * dVPN browser-proxy: route THIS browser through a chosen exit location via chrome.proxy.
//     Honest scope — browser-only, single-hop, not a system VPN and not Tor.

const GATEWAY = 'https://animica.dev';
const API = GATEWAY + '/api/mkt/v1/names';
const PROXY_KEY = 'anmvpn.activeProxy'; // { exitId, label, host, port, token }

function escapeXml(s) {
  return String(s || '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]
  ));
}

/* ----------------------------------------------------------------- omnibox */

chrome.omnibox.setDefaultSuggestion({
  description: 'Search the Animica <match>.anm</match> index — or type a name to visit it',
});

chrome.omnibox.onInputChanged.addListener((text, suggest) => {
  const q = text.trim().replace(/\.anm$/, '');
  if (!q) return;
  fetch(`${API}?search=${encodeURIComponent(q)}`)
    .then((r) => r.json())
    .then((d) => {
      const items = (d.results || []).slice(0, 7).map((x) => {
        let desc = x.kind;
        try { desc = JSON.parse(x.recordsJson).description || x.kind; } catch {}
        return {
          content: x.name,
          description: `<url>${escapeXml(x.name)}.anm</url> — <dim>${escapeXml(desc)}</dim>`,
        };
      });
      suggest(items);
    })
    .catch(() => {});
});

chrome.omnibox.onInputEntered.addListener((text, disposition) => {
  const q = text.trim().replace(/\.anm$/, '');
  const url = /^[a-z0-9-]{2,63}$/.test(q)
    ? `${GATEWAY}/anm/${q}`
    : `${GATEWAY}/names?search=${encodeURIComponent(q)}`;
  if (disposition === 'newForegroundTab') chrome.tabs.create({ url });
  else if (disposition === 'newBackgroundTab') chrome.tabs.create({ url, active: false });
  else chrome.tabs.update({ url });
});

/* ------------------------------------------------------------ dVPN proxy */

// Supply Basic proxy credentials for exits that require a token. Registered only while a
// token-bearing proxy is active; MV3 service workers restart, so we (re)wire it on demand.
let _authProxy = null; // { host, port, token }
function _onAuthRequired(details, callback) {
  const p = _authProxy;
  if (p && details.isProxy && details.challenger &&
      details.challenger.host === p.host && Number(details.challenger.port) === Number(p.port)) {
    return callback({ authCredentials: { username: 'anm', password: p.token } });
  }
  return callback(); // not our proxy — let the browser handle it
}

async function wireAuth(proxy) {
  _authProxy = proxy && proxy.token ? { host: proxy.host, port: proxy.port, token: proxy.token } : null;
  if (!_authProxy) {
    if (chrome.webRequest && chrome.webRequest.onAuthRequired.hasListener(_onAuthRequired)) {
      chrome.webRequest.onAuthRequired.removeListener(_onAuthRequired);
    }
    return;
  }
  // Requires the optional "webRequest"/"webRequestAuthProvider" perms (requested from the popup).
  const granted = await chrome.permissions.contains({ permissions: ['webRequest', 'webRequestAuthProvider'] });
  if (granted && chrome.webRequest && !chrome.webRequest.onAuthRequired.hasListener(_onAuthRequired)) {
    chrome.webRequest.onAuthRequired.addListener(_onAuthRequired, { urls: ['<all_urls>'] }, ['blocking']);
  }
}

function applyProxy(proxy) {
  return new Promise((resolve, reject) => {
    const config = {
      mode: 'fixed_servers',
      rules: {
        singleProxy: { scheme: 'http', host: proxy.host, port: Number(proxy.port) },
        // Bypass ONLY genuinely-local destinations so the user's LAN/loopback is never shipped to
        // an untrusted exit. Note we deliberately DO NOT bypass animica.dev or *.anm: routing them
        // through the exit is what stops the registry (which knows the wallet) from also learning
        // the user's real IP — bypassing them would deanonymise every .anm visit.
        bypassList: [
          'localhost', '127.0.0.1/8', '[::1]',
          '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
          '169.254.0.0/16', '100.64.0.0/10',
          'fc00::/7', 'fe80::/10',
          '<local>', // dotless intranet hostnames
        ],
      },
    };
    chrome.proxy.settings.set({ value: config, scope: 'regular' }, () => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      // Verify WE actually control the proxy. Another extension installed later can seize
      // chrome.proxy and silently disable the tunnel while our UI still shows "connected";
      // read the setting back and refuse to claim protection unless this extension controls it.
      chrome.proxy.settings.get({ incognito: false }, (details) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        const level = details && details.levelOfControl;
        if (level !== 'controlled_by_this_extension') {
          return reject(new Error(
            `proxy is ${level || 'not controlled by this extension'} — another extension may be ` +
            `overriding the VPN. Disable it or grant this extension proxy control.`));
        }
        resolve();
      });
    });
  });
}

function clearProxySettings() {
  return new Promise((resolve) => {
    chrome.proxy.settings.clear({ scope: 'regular' }, () => resolve());
  });
}

async function setProxy(proxy) {
  await applyProxy(proxy);
  await wireAuth(proxy);
  await chrome.storage.local.set({ [PROXY_KEY]: proxy });
  updateBadge(proxy);
}

async function clearProxy() {
  await clearProxySettings();
  await wireAuth(null);
  await chrome.storage.local.remove(PROXY_KEY);
  updateBadge(null);
}

function updateBadge(proxy) {
  try {
    chrome.action.setBadgeText({ text: proxy ? 'VPN' : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#2b6ef6' });
    chrome.action.setTitle({
      title: proxy ? `Animica — routed via ${proxy.label || proxy.host} (browser proxy)`
                   : 'Animica — .anm search & dVPN',
    });
  } catch {}
}

// Re-arm the auth listener + badge after a service-worker restart.
async function rehydrate() {
  const got = await chrome.storage.local.get(PROXY_KEY);
  const proxy = got[PROXY_KEY];
  if (proxy) { await wireAuth(proxy); updateBadge(proxy); }
}
chrome.runtime.onStartup.addListener(rehydrate);
chrome.runtime.onInstalled.addListener(rehydrate);
rehydrate();

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === 'vpn.set') { await setProxy(msg.proxy); sendResponse({ ok: true }); }
      else if (msg.type === 'vpn.clear') { await clearProxy(); sendResponse({ ok: true }); }
      else if (msg.type === 'vpn.get') {
        const got = await chrome.storage.local.get(PROXY_KEY);
        sendResponse({ ok: true, proxy: got[PROXY_KEY] || null });
      } else sendResponse({ ok: false, error: 'unknown message' });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // async response
});
