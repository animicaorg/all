import { WalletPanel } from "./WalletPanel";
import { ChatWorkspace } from "./ChatWorkspace";
import { getSessionUser } from "@/src/server/auth/session";
import { prisma } from "@/src/server/db/prisma";
import { bootRpcDiscovery } from "@/src/server/rpc/animicaRpc";

export default async function IdeHomePage() {
  const user = await getSessionUser();
  const sub = user ? await prisma.subscription.findFirst({ where: { userId: user.id, status: "ACTIVE" } }) : null;
  await bootRpcDiscovery().catch(() => null);

  return (
    <div className="space-y-4">
      <WalletPanel />
      <ChatWorkspace demoMode={!sub} />
    </div>
  );
}
