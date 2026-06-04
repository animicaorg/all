"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api("/api/auth/register", { method: "POST", body: { email, password, displayName } });
      window.location.href = "/dashboard";
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md space-y-5">
      <h1 className="text-2xl font-semibold">Create account</h1>
      <form onSubmit={submit} className="card space-y-3">
        <input className="field" placeholder="display name (optional)" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <input className="field" type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input className="field" type="password" placeholder="password (min 8)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        {err && <p className="text-sm text-red-400">{err}</p>}
        <button className="btn-primary w-full" disabled={busy}>{busy ? "…" : "Create account"}</button>
      </form>
      <p className="text-sm text-white/60">Have an account? <Link href="/login" className="text-neon-blue">Sign in</Link></p>
    </div>
  );
}
