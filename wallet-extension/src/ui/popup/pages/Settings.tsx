import React, { useEffect, useMemo, useState } from "react";

type Settings = {
  theme: "system" | "light" | "dark";
  lang: "en" | "es";
  defaultAlgo: "dilithium3" | "sphincs-shake-128s";
  autoLockMins: number;          // 0 = never
  showTestnets: boolean;
  requestLogging: boolean;
};

const DEFAULTS: Settings = {
  theme: "system",
  lang: "en",
  defaultAlgo: "dilithium3",
  autoLockMins: 15,
  showTestnets: true,
  requestLogging: false,
};

function callBackground<T = any>(msg: any): Promise<T | undefined> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp) => {
        if (resp && typeof resp === "object" && "ok" in resp && "result" in resp) {
          resolve((resp as any).ok ? ((resp as any).result as T) : (resp as T));
          return;
        }
        resolve(resp as T);
      });
    } catch {
      resolve(undefined);
    }
  });
}

async function readLocalSettings(): Promise<Settings> {
  try {
    const raw = await chrome.storage?.local.get(["settings"]);
    const s = raw?.settings as Partial<Settings> | undefined;
    return { ...DEFAULTS, ...(s || {}) };
  } catch {
    return DEFAULTS;
  }
}

async function writeLocalSettings(s: Settings) {
  try {
    await chrome.storage?.local.set({ settings: s });
  } catch {
    // noop
  }
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);

  const themeLabel = useMemo(() => {
    switch (settings.theme) {
      case "light":
        return "Light";
      case "dark":
        return "Dark";
      default:
        return "System";
    }
  }, [settings.theme]);

  // Load settings (prefer background, fallback to local storage)
  useEffect(() => {
    (async () => {
      const fromBg = await callBackground<Settings | { ok: boolean; error?: string }>({ type: "settings.get" });
      if (fromBg && typeof fromBg === "object" && (fromBg as any).ok !== false) {
        setSettings({ ...DEFAULTS, ...fromBg });
        setLoaded(true);
        return;
      }
      const local = await readLocalSettings();
      setSettings(local);
      setLoaded(true);
    })();
  }, []);

  // Persist + notify background
  const save = async (next: Settings) => {
    setSaving(true);
    setSettings(next);
    // Write to local storage
    await writeLocalSettings(next);
    // Best-effort notify background (if handler exists)
    await callBackground({ type: "settings.update", patch: next });
    setSaving(false);
    setLastSavedAt(Date.now());
    // Apply theme immediately in popup DOM
    applyTheme(next.theme);
  };

  const patch = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    save({ ...settings, [key]: value });

  const applyTheme = (t: Settings["theme"]) => {
    const root = document.documentElement;
    root.dataset.theme =
      t === "system"
        ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
        : t;
  };

  useEffect(() => {
    if (loaded) applyTheme(settings.theme);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  const lockNow = async () => {
    await callBackground({ type: "wallet.lock" });
    window.close();
  };

  const clearPermissions = async () => {
    if (!confirm("Clear dapp connection approvals?")) return;
    const ok = await callBackground<{ ok: boolean }>({ type: "permissions.clear" });
    if (!ok?.ok) {
      // Fallback: clear local namespace if background handler absent
      await chrome.storage?.local.remove(["permissions", "sessions"]);
    }
    alert("Permissions cleared.");
  };

  const clearCaches = async () => {
    const ok = await callBackground<{ ok: boolean }>({ type: "cache.clear" });
    if (!ok?.ok) {
      await chrome.storage?.local.remove(["rpcCache", "simCache"]);
    }
    alert("Caches cleared.");
  };

  const hardReset = async () => {
    if (!confirm("This will sign you out and remove all local data (vault remains encrypted). Continue?")) return;
    if (!confirm("Are you absolutely sure? You will need your mnemonic to restore.")) return;
    await callBackground({ type: "storage.reset" });
    await chrome.storage?.local.clear();
    alert("Extension reset. Please reopen and onboard again.");
    window.close();
  };

  const onExportVault = async () => {
    const res = await callBackground<{ fileName?: string; dataUrl?: string; error?: string; ok?: boolean }>({
      route: "vault.export",
    });
    if (!res || ("ok" in res && (res as any).ok === false)) {
      const errMsg = (res as any)?.error || "Export failed (is wallet unlocked?)";
      alert(errMsg);
      return;
    }
    const payload = "result" in (res as any) ? (res as any).result : res;
    // Fallback: if background didn't give dataUrl, fetch from local
    if (!payload?.dataUrl) {
      const raw = await chrome.storage?.local.get(["vault"]);
      const blob = new Blob([JSON.stringify(raw?.vault ?? {}, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      triggerDownload(url, payload?.fileName ?? "animica-vault.json");
      return;
    }
    triggerDownload(payload.dataUrl, payload.fileName ?? "animica-vault.json");
  };

  const triggerDownload = (url: string, filename: string) => {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      a.remove();
      if (url.startsWith("blob:")) URL.revokeObjectURL(url);
    }, 0);
  };

  return (
    <section className="ami-settings">
      <div className="ami-section-header">
        <h2 className="ami-section-title">Settings</h2>
        {saving ? (
          <span className="ami-dim">Saving…</span>
        ) : lastSavedAt ? (
          <span className="ami-dim">Saved</span>
        ) : null}
      </div>

      {!loaded && <div className="ami-card">Loading…</div>}

      {loaded && (
        <>
          <div className="ami-grid-2">
            <div className="ami-card">
              <div className="ami-card-title">General</div>

              <label className="ami-label">Theme</label>
              <select
                className="ami-input"
                value={settings.theme}
                onChange={(e) => patch("theme", e.target.value as Settings["theme"])}
              >
                <option value="system">System</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
              <div className="ami-hint">Current: {themeLabel}</div>

              <label className="ami-label">Language</label>
              <select
                className="ami-input"
                value={settings.lang}
                onChange={(e) => patch("lang", e.target.value as Settings["lang"])}
              >
                <option value="en">English</option>
                <option value="es">Español</option>
              </select>

              <label className="ami-label">Default signature algorithm</label>
              <select
                className="ami-input"
                value={settings.defaultAlgo}
                onChange={(e) => patch("defaultAlgo", e.target.value as Settings["defaultAlgo"])}
              >
                <option value="dilithium3">CRYSTALS-Dilithium3</option>
                <option value="sphincs-shake-128s">SPHINCS+ SHAKE-128s</option>
              </select>

              <label className="ami-label">Auto-lock (minutes)</label>
              <input
                className="ami-input"
                type="number"
                min={0}
                max={120}
                value={settings.autoLockMins}
                onChange={(e) => patch("autoLockMins", Math.max(0, Math.min(120, Number(e.target.value || 0))))}
              />
              <div className="ami-hint">Set 0 to disable auto-lock (not recommended).</div>
            </div>

            <div className="ami-card">
              <div className="ami-card-title">Developer</div>

              <label className="ami-switch">
                <input
                  type="checkbox"
                  checked={settings.showTestnets}
                  onChange={(e) => patch("showTestnets", e.target.checked)}
                />
                <span>Show testnets</span>
              </label>

              <label className="ami-switch">
                <input
                  type="checkbox"
                  checked={settings.requestLogging}
                  onChange={(e) => patch("requestLogging", e.target.checked)}
                />
                <span>Log provider requests (popup only)</span>
              </label>

              <div className="ami-actions">
                <button className="ami-btn" onClick={clearCaches}>Clear caches</button>
                <button className="ami-btn" onClick={clearPermissions}>Clear dapp approvals</button>
              </div>
            </div>
          </div>

          <div className="ami-grid-2">
            <div className="ami-card">
              <div className="ami-card-title">Security</div>
              <div className="ami-actions">
                <button className="ami-btn" onClick={lockNow}>Lock now</button>
                <button className="ami-btn" onClick={onExportVault}>Export encrypted vault</button>
              </div>
              <ul className="ami-list">
                <li>Your mnemonic is encrypted in the vault. Keep exports safe.</li>
                <li>Never share exports; they can restore your accounts.</li>
              </ul>
            </div>

            <div className="ami-card ami-card-danger">
              <div className="ami-card-title">Danger zone</div>
              <p className="ami-hint">
                Reset removes local data and signs you out. You will need your mnemonic to restore.
              </p>
              <button className="ami-btn ami-btn-danger" onClick={hardReset}>Reset extension</button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
