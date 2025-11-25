import React, { useEffect, useMemo, useState } from "react";

type Props = {
  words?: 12 | 24;
  initialMnemonic?: string;
  isBusy?: boolean;
  onBack: () => void;
  onNext: (mnemonic: string) => void;
};

type BgGenerateReq = {
  type: "keyring.generateMnemonic";
  words: 12 | 24;
};

type BgGenerateResp =
  | { ok: true; mnemonic: string }
  | { ok: false; error: string };

/**
 * CreateMnemonic
 * - Requests a mnemonic from the background keyring (preferred)
 * - Dev fallback generates a random *placeholder* phrase (NOT FOR PRODUCTION KEYS)
 * - Renders a numbered grid, copy & download helpers, and a confirmation gate
 */
export default function CreateMnemonic({
  words = 12,
  initialMnemonic,
  isBusy = false,
  onBack,
  onNext,
}: Props) {
  const [mnemonic, setMnemonic] = useState<string>(initialMnemonic ?? "");
  const [ack, setAck] = useState(false);
  const [loading, setLoading] = useState<boolean>(!initialMnemonic);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<boolean>(true);

  // Attempt to fetch a real mnemonic from background on first mount (unless provided)
  useEffect(() => {
    if (initialMnemonic) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const req: BgGenerateReq = { type: "keyring.generateMnemonic", words };
        const resp = await sendBg<BgGenerateResp>(req);
        if (cancelled) return;
        if (resp?.ok) {
          setMnemonic(resp.mnemonic);
          setError(null);
        } else {
          // Fallback to a dev-only placeholder if background route unavailable
          const ph = devPlaceholderMnemonic(words);
          setMnemonic(ph);
          setError(
            "Background keyring unavailable — using DEV placeholder mnemonic (not for mainnet)."
          );
        }
      } catch (e: any) {
        if (cancelled) return;
        setMnemonic(devPlaceholderMnemonic(words));
        setError(
          "Could not reach background — using DEV placeholder mnemonic (not for mainnet)."
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialMnemonic, words]);

  const wordsList = useMemo(() => (mnemonic ? mnemonic.trim().split(/\s+/) : []), [mnemonic]);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(mnemonic);
    } catch {
      // ignore
    }
  };

  const onDownload = () => {
    const blob = new Blob(
      [
        [
          "# Animica Wallet — Recovery Phrase\n",
          "# Store this offline. Anyone with these words can control your funds.\n\n",
          mnemonic,
          "\n",
        ].join(""),
      ],
      { type: "text/plain;charset=utf-8" }
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const ts = new Date().toISOString().slice(0, 10);
    a.download = `animica-recovery-${ts}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const canContinue = ack && !loading && !!mnemonic && !isBusy;

  return (
    <section className="ob-card" data-testid="create-mnemonic">
      <h1 className="ob-title">Write down your recovery phrase</h1>
      <p className="ob-subtitle">
        This {words}-word phrase backs up your wallet. Store it securely and never share it.
      </p>

      {error && <p className="ob-warning" role="alert">{error}</p>}

      <div className={"mnemonic-wrap" + (revealed ? "" : " blurred")}>
        {loading ? (
          <div className="mnemonic-skeleton" aria-busy="true" />
        ) : (
          <ol className="mnemonic-grid">
            {wordsList.map((w, i) => (
              <li key={i}>
                <span className="idx">{i + 1}</span>
                <span className="word">{w}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="ob-actions row">
        <button className="btn" onClick={() => setRevealed((v) => !v)} disabled={loading}>
          {revealed ? "Hide" : "Reveal"}
        </button>
        <button className="btn" onClick={onCopy} disabled={loading || !mnemonic}>
          Copy
        </button>
        <button className="btn" onClick={onDownload} disabled={loading || !mnemonic}>
          Download .txt
        </button>
      </div>

      <label className="ob-check">
        <input
          type="checkbox"
          checked={ack}
          onChange={(e) => setAck(e.target.checked)}
          disabled={loading}
        />
        I have written these words down in order and will keep them in a safe place.
      </label>

      <div className="ob-actions">
        <button className="btn" onClick={onBack} disabled={loading || isBusy}>
          Back
        </button>
        <button
          className="btn primary"
          onClick={() => onNext(mnemonic)}
          disabled={!canContinue}
          data-testid="btn-continue-verify"
        >
          Continue to verification
        </button>
      </div>

      <p className="ob-hint">
        Never share your recovery phrase with anyone. The wallet team will never ask for it.
      </p>
    </section>
  );
}

/** Send a message to the background service worker (MV3). */
async function sendBg<T = any>(payload: any): Promise<T> {
  // @ts-expect-error: chrome is injected in extension context
  if (typeof chrome !== "undefined" && chrome?.runtime?.sendMessage) {
    return await new Promise<T>((resolve, reject) => {
      try {
        // @ts-expect-error
        chrome.runtime.sendMessage(payload, (resp: T) => {
          const err = chrome.runtime.lastError;
          if (err) reject(err);
          else resolve(resp);
        });
      } catch (e) {
        reject(e);
      }
    });
  }
  throw new Error("chrome.runtime not available");
}

/**
 * DEV-ONLY fallback: generate a placeholder mnemonic by sampling a small wordlist.
 * This is NOT BIP-39 and has NO checksum — only used when background is unreachable
 * (e.g., unit tests or storybook). Do not use for real accounts.
 */
function devPlaceholderMnemonic(words: 12 | 24): string {
  const wl = DEV_WORDLIST;
  const out: string[] = [];
  const rv = new Uint32Array(words);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(rv);
    for (let i = 0; i < words; i++) out.push(wl[rv[i] % wl.length]);
  } else {
    for (let i = 0; i < words; i++) out.push(wl[(Math.random() * wl.length) | 0]);
  }
  return out.join(" ");
}

const DEV_WORDLIST = [
  "able","absorb","access","across","act","agent","aim","alpha","anchor","ancient","angle","answer",
  "apple","area","arrow","asset","atom","attend","audit","august","aunt","auto","axis","azure",
  "badge","baker","balance","bamboo","banner","barrel","basic","battery","beacon","beauty","become",
  "before","behind","bench","beyond","bitter","black","blade","blanket","blend","blossom","blue",
  "bold","bonus","border","borrow","bottom","bounce","brain","brand","brave","breeze","bridge",
  "bright","bring","brother","brown","bubble","budget","build","bullet","bunker","burden","butter",
  "cabin","cable","cactus","cake","camera","cancel","canyon","carbon","cargo","carpet","carry",
  "castle","casual","catalog","catch","cause","celery","center","century","ceramic","chalk","chance",
  "chaos","chapter","charge","charm","chat","cheap","check","cherry","chest","chief","choice","choose",
  "circle","city","civil","claim","clarify","class","clean","cliff","climb","clinic","clock","close",
  "cloth","cloud","clutch","coach","coast","coconut","code","coffee","coin","color","column","combine",
  "comfort","comic","common","company","concert","concrete","conduct","confirm","connect","consider",
  "control","cook","cool","copper","copy","coral","corner","correct","cost","cotton","couch","country",
  "couple","course","cousin","cover","coyote","crack","craft","crash","crater","crazy","cream","credit",
  "creek","crisp","critic","cross","crouch","crowd","crucial","crumble","crystal","cube","culture",
  "cupboard","curious","current","curve","cushion","custom","cycle","dad","daily","dawn","deal","debris",
  "decide","define","degree","delay","deliver","demand","denial","depth","design","desk","detail","detect",
  "device","diagram","diary","diesel","differ","digital","dinner","direct","discover","distance","doctor",
  "dolphin","domain","donate","donkey","double","dragon","drama","draw","dream","dress","drift","drive",
  "drop","drum","dry","duck","dune","during","eager","early","earth","easel","east","easy","echo","edge",
  "edit","educate","effort","eight","either","elbow","elder","electric","elegant","element","elevator",
  "elite","ember","emotion","employ","empty","enable","endorse","energy","enforce","engine","enjoy",
  "enough","enrich","enroll","ensure","enter","entire","entry","equal","equip","era","erase","erosion",
  "error","erupt","escape","essay","estate","eternal","ethical","even","evidence","evil","evolve","exact",
  "example","exceed","exchange","exclude","excuse","execute","exercise","exhaust","exhibit","exist","exit",
  "exotic","expand","expect","expire","explain","expose","express","extend","extra","eye","fabric","face",
  "faculty","fade","faint","faith","famous","fancy","farmer","fashion","fast","father","favorite","feature",
  "february","federal","fee","feed","fellow","fence","festival","fetch","fever","fiber","fiction","field",
  "figure","file","film","filter","final","find","finger","finish","fire","firm","first","fiscal","fish",
  "fit","fitness","fix","flag","flame","flash","flat","flavor","flee","flight","flip","float","flock",
  "floor","flower","fluid","focus","fog","foil","fold","forest","forget","fork","fortune","found","fox",
  "fragile","frame","frequent","fresh","friend","frog","front","frost","frown","frozen","fruit","fuel",
  "fun","furnace","galaxy","gallery","gamma","garage","garlic","gather","gauge","gaze","general","genius",
  "gentle","genuine","giant","gift","ginger","giraffe","girl","give","glad","glance","glare","glass",
  "glide","globe","gloom","glory","glove","glow","gold","good","goose","gorilla","gospel","gossip","govern",
  "grace","grain","grant","grape","grass","gravity","great","green","grid","grief","grit","grocery","group",
  "grow","guard","guess","guide","guilt","habit","hammer","hand","happy","harbor","harvest","hat","hawk",
  "hazard","head","health","heart","heavy","height","hello","helmet","help","hen","hero","hidden","high",
];

/* Minimal styles expectation (class names used here)
.ob-card { padding: 16px; }
.ob-title { font-weight: 600; font-size: 20px; margin: 0 0 8px; }
.ob-subtitle { opacity: 0.85; margin: 0 0 12px; }
.ob-warning { background: #fff3cd; color: #8a6d3b; padding: 8px 10px; border-radius: 6px; margin: 8px 0 12px; }
.mnemonic-wrap { border: 1px solid var(--border, #ddd); border-radius: 10px; padding: 12px; background: var(--bgElev, #fafafa); }
.mnemonic-wrap.blurred { filter: blur(6px); }
.mnemonic-grid { columns: 2; column-gap: 16px; list-style: none; padding: 0; margin: 0; }
.mnemonic-grid li { break-inside: avoid; display: flex; gap: 8px; padding: 4px 2px; }
.mnemonic-grid .idx { width: 20px; opacity: 0.6; text-align: right; }
.mnemonic-grid .word { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.ob-actions { display: flex; gap: 8px; margin-top: 12px; }
.ob-actions.row { justify-content: flex-start; }
.btn { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border,#ddd); background: var(--btnBg,#fff); cursor: pointer; }
.btn.primary { background: var(--primary,#111); color: #fff; border-color: var(--primary,#111); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ob-check { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
.ob-hint { opacity: 0.7; font-size: 12px; margin-top: 8px; }
*/
