import { getSessionUser } from "@/server/auth";
import { getBalance } from "@/server/credit";
import { prisma } from "@/server/db";
import { fmtFixed } from "@/lib/money";
import { KeyManager } from "./KeyManager";
import { revokeKeyAction } from "./actions";

export const dynamic = "force-dynamic";

export default async function KeysPage() {
  const user = await getSessionUser();
  if (!user) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
        <a href="/app/login" className="text-blue-400">Sign in</a> to manage your API keys.
      </p>
    );
  }

  const [keys, balance] = await Promise.all([
    prisma.apiKey.findMany({
      where: { userId: user.id },
      orderBy: { createdAt: "desc" },
    }),
    getBalance(user.id),
  ]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">API keys</h1>
        <span className="text-sm text-white/60">
          Credit balance: <span className="text-emerald-300">${fmtFixed(balance, 4)}</span>
        </span>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4">
        <KeyManager />
      </div>

      <div className="space-y-2">
        {keys.length === 0 && (
          <p className="text-white/60">No keys yet — create one above.</p>
        )}
        {keys.map((k) => (
          <div
            key={k.id}
            className="flex items-center justify-between rounded-xl border border-white/10 bg-white/5 p-4"
          >
            <div>
              <code className="text-sm">{k.prefix}…</code>
              <span className="ml-2 text-xs text-white/40">
                {k.label ?? "unlabelled"} · {k.isActive ? "active" : "revoked"}
                {k.lastUsedAt ? ` · last used ${k.lastUsedAt.toISOString().slice(0, 10)}` : ""}
              </span>
            </div>
            {k.isActive && (
              <form action={revokeKeyAction}>
                <input type="hidden" name="id" value={k.id} />
                <button
                  type="submit"
                  className="rounded-lg border border-red-500/30 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/10"
                >
                  Revoke
                </button>
              </form>
            )}
          </div>
        ))}
      </div>

      <p className="text-sm text-white/50">
        Base URL: <code>https://pool.animica.org/v1</code> — OpenAI-compatible.
        See <a href="/v1/models" className="text-blue-400">/v1/models</a>.
      </p>
    </div>
  );
}
