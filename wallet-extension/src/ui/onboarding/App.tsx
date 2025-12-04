import React, { useCallback, useEffect, useMemo, useState } from "react";
import Welcome from "./pages/Welcome";
import CreateMnemonic from "./pages/CreateMnemonic";
import VerifyMnemonic from "./pages/VerifyMnemonic";
import Import from "./pages/Import";
import Finish from "./pages/Finish";

// Minimal message shapes used to talk to the background SW.
// Router there will map these `kind` strings to handlers.
type BgMessage =
  | { kind: "keyring.generateMnemonic"; words?: number }
  | { kind: "keyring.setupVault"; action: "create" | "import"; mnemonic: string; pin?: string }
  | { kind: "sessions.reset" };

async function bgSend<T = unknown>(msg: BgMessage): Promise<T> {
  if (!(globalThis as any).chrome?.runtime?.id) {
    throw new Error("Background runtime unavailable (extension not loaded?)");
  }
  return chrome.runtime.sendMessage(msg) as Promise<T>;
}

type Step = "welcome" | "create" | "verify" | "import" | "finish";

export default function App() {
  const [step, setStep] = useState<Step>("welcome");
  const [mode, setMode] = useState<"new" | "import" | null>(null);
  const [mnemonic, setMnemonic] = useState<string>("");
  const [pin, setPin] = useState<string>("");
  const [pin2, setPin2] = useState<string>("");
  const [isBusy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const initialMode = useMemo(() => {
    try {
      return new URLSearchParams(window.location.search).get("mode");
    } catch {
      return null;
    }
  }, []);

  // Derived: can we finalize?
  const pinOk = useMemo(() => pin.length >= 6 && pin === pin2, [pin, pin2]);

  const beginCreate = useCallback(async () => {
    setMode("new");
    setError(null);
    setBusy(true);
    try {
      const res = await bgSend<{ mnemonic: string }>({ kind: "keyring.generateMnemonic", words: 24 });
      setMnemonic(res.mnemonic);
      setStep("create");
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const beginImport = useCallback(() => {
    setMode("import");
    setError(null);
    setStep("import");
  }, []);

  const onCreatedContinue = useCallback(() => {
    // After showing the generated phrase, proceed to verify
    setStep("verify");
  }, []);

  const onVerifiedContinue = useCallback(() => {
    setStep("finish");
  }, []);

  const onImportSubmit = useCallback((phrase: string) => {
    setMnemonic(phrase.trim().replace(/\s+/g, " "));
    setStep("finish");
  }, []);

  const onFinalize = useCallback(async () => {
    setError(null);
    if (!mode) return;
    if (!pinOk) {
      setError("PINs do not match or are too short (min 6).");
      return;
    }
    if (!mnemonic) {
      setError("Recovery phrase missing.");
      return;
    }
    setBusy(true);
    try {
      await bgSend<{ ok: true }>({
        kind: "keyring.setupVault",
        action: mode === "new" ? "create" : "import",
        mnemonic,
        pin,
      });
      // Reset any stale sessions so popup starts clean
      await bgSend({ kind: "sessions.reset" });
      // Success — Finish screen will offer to close the window.
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }, [mode, pinOk, mnemonic, pin]);

  useEffect(() => {
    if (step !== "welcome" || !initialMode) return;
    if (initialMode === "create") {
      void beginCreate();
    } else if (initialMode === "import") {
      beginImport();
    }
  }, [beginCreate, beginImport, initialMode, step]);

  return (
    <div className="onboarding-shell">
      <header className="ob-header">
        <div className="brand">
          <span className="logo">◎</span>
          <span className="title">Animica Wallet</span>
        </div>
        <div className="phase">
          {step === "welcome" && "Welcome"}
          {step === "create" && "Your recovery phrase"}
          {step === "verify" && "Verify phrase"}
          {step === "import" && "Import phrase"}
          {step === "finish" && "Set PIN & Finish"}
        </div>
      </header>

      {error && <div className="ob-error" role="alert">{error}</div>}

      <main className="ob-main">
        {step === "welcome" && (
          <Welcome
            isBusy={isBusy}
            onCreate={beginCreate}
            onImport={beginImport}
          />
        )}

        {step === "create" && (
          <CreateMnemonic
            mnemonic={mnemonic}
            onContinue={onCreatedContinue}
            onBack={() => setStep("welcome")}
          />
        )}

        {step === "verify" && (
          <VerifyMnemonic
            mnemonic={mnemonic}
            onVerified={onVerifiedContinue}
            onBack={() => setStep("create")}
          />
        )}

        {step === "import" && (
          <Import
            onSubmit={onImportSubmit}
            onBack={() => setStep("welcome")}
          />
        )}

        {step === "finish" && (
          <Finish
            mode={mode ?? "new"}
            mnemonicPreview={mnemonic}
            pin={pin}
            pin2={pin2}
            setPin={setPin}
            setPin2={setPin2}
            isBusy={isBusy}
            canFinish={pinOk && !!mnemonic}
            onFinish={onFinalize}
            onBack={() => setStep(mode === "new" ? "verify" : "import")}
          />
        )}
      </main>

      <footer className="ob-footer">
        <small>
          Never share your recovery phrase. We never send it off-device.{" "}
          <a href="https://animica.dev/security" target="_blank" rel="noreferrer">Learn more</a>
        </small>
      </footer>
    </div>
  );
}
