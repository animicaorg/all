import { getSessionUser } from "@/src/server/auth/session";
import { prisma } from "@/src/server/db/prisma";

export default async function AccountPage() {
  const user = await getSessionUser();
  if (!user) return <div className="card">Please sign in first.</div>;

  const sub = await prisma.subscription.findFirst({ where: { userId: user.id } });
  const usage = await prisma.usageDaily.findFirst({ where: { userId: user.id }, orderBy: { day: "desc" } });
  const wallet = await prisma.walletSession.findFirst({ where: { userId: user.id, status: "active" }, orderBy: { createdAt: "desc" } });

  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold">Account</h1>
      <div className="grid gap-2">
        <div className="card">Subscription: {sub?.status ?? "none"} ({sub?.subscriptionId ?? "n/a"})</div>
        <div className="card">Usage today: {usage?.messageCount ?? 0}/200</div>
        <div className="card">Wallet session: {wallet ? `${wallet.type} • ${wallet.accounts[0]}` : "none"}</div>
      </div>
    </div>
  );
}
