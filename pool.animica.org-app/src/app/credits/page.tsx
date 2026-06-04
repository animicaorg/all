import { getSessionUser } from "@/server/auth";
import { getBalance } from "@/server/credit";
import { prisma } from "@/server/db";
import { fmtFixed } from "@/lib/money";
import { BuyCredits } from "./BuyCredits";

export const dynamic = "force-dynamic";

export default async function CreditsPage() {
  const user = await getSessionUser();
  if (!user) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
        <a href="/app/login" className="text-blue-400">Sign in</a> to buy inference credits.
      </p>
    );
  }

  const [balance, purchases] = await Promise.all([
    getBalance(user.id),
    prisma.creditPurchase.findMany({
      where: { userId: user.id },
      orderBy: { createdAt: "desc" },
      take: 20,
    }),
  ]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Credits</h1>
        <span className="text-sm text-white/60">
          Balance: <span className="text-emerald-300">${fmtFixed(balance, 4)}</span>
        </span>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4">
        <BuyCredits />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-medium text-white/70">Recent purchases</h2>
        {purchases.length === 0 && <p className="text-white/50">No purchases yet.</p>}
        {purchases.map((p) => (
          <div
            key={p.id}
            className="flex items-center justify-between rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm"
          >
            <span>${p.amountUsd}</span>
            <span className="text-white/50">
              {p.payCurrency ? p.payCurrency.toUpperCase() + " · " : ""}
              {p.createdAt.toISOString().slice(0, 10)}
            </span>
            <span
              className={
                p.status === "paid"
                  ? "text-emerald-300"
                  : p.status === "pending"
                    ? "text-amber-300"
                    : "text-red-300"
              }
            >
              {p.status}
            </span>
            {p.status === "pending" && p.invoiceUrl && (
              <a href={p.invoiceUrl} className="text-blue-400">
                Resume payment →
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
