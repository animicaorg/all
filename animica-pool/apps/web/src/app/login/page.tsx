"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api("/api/auth/login", { method: "POST", body: { email, password } });
      window.location.href = "/dashboard";
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md space-y-5">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <form onSubmit={submit} className="card space-y-3">
        <input className="field" type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input className="field" type="password" placeholder="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {err && <p className="text-sm text-red-400">{err}</p>}
        <button className="btn-primary w-full" disabled={busy}>{busy ? "…" : "Sign in"}</button>
      </form>
      <p className="text-sm text-white/60">No account? <Link href="/register" className="text-neon-blue">Register</Link></p>
    </div>
  );
}
