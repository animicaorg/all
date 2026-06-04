import { getSessionUser } from "@/server/auth";
import { getBalance } from "@/server/credit";
import { prisma } from "@/server/db";
import { fmtFixed } from "@/lib/money";

export const dynamic = "force-dynamic";

export default async function UsagePage() {
  const user = await getSessionUser();
  if (!user) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
        <a href="/app/login" className="text-blue-400">Sign in</a> to view usage.
      </p>
    );
  }

  const [balance, requests] = await Promise.all([
    getBalance(user.id),
    prisma.inferenceRequest.findMany({
      where: { userId: user.id },
      orderBy: { createdAt: "desc" },
      take: 50,
    }),
  ]);

  const spent = requests
    .filter((r) => r.status === "success")
    .reduce((acc, r) => acc + Number(r.customerCostUsd), 0);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Usage</h1>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Credit balance" value={`$${fmtFixed(balance, 4)}`} />
        <Stat label="Recent spend" value={`$${spent.toFixed(4)}`} />
        <Stat label="Recent calls" value={String(requests.length)} />
      </div>

      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full text-left text-sm">
          <thead className="bg-white/5 text-white/60">
            <tr>
              <th className="px-3 py-2">When</th>
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Provider</th>
              <th className="px-3 py-2">Tokens</th>
              <th className="px-3 py-2">Cost</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {requests.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-white/50">
                  No inference calls yet.
                </td>
              </tr>
            )}
            {requests.map((r) => (
              <tr key={r.id} className="border-t border-white/5">
                <td className="px-3 py-2 text-white/50">
                  {r.createdAt.toISOString().slice(0, 19).replace("T", " ")}
                </td>
                <td className="px-3 py-2">{r.model}</td>
                <td className="px-3 py-2 text-white/60">{r.provider}</td>
                <td className="px-3 py-2 text-white/60">
                  {r.inputTokens}/{r.outputTokens}
                </td>
                <td className="px-3 py-2">${fmtFixed(r.customerCostUsd, 6)}</td>
                <td className="px-3 py-2">
                  <span className={r.status === "success" ? "text-emerald-300" : "text-red-300"}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      <div className="text-xs text-white/50">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}
