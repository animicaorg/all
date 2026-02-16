import { prisma } from "@/src/server/db/prisma";
import { env } from "@/src/server/env";

export default async function DeploysPage({ searchParams }: { searchParams: { tx?: string } }) {
  const deploys = await prisma.deployJob.findMany({ orderBy: { createdAt: "desc" }, take: 25 });
  const selected = searchParams.tx ? deploys.find((d) => d.txHash === searchParams.tx) : null;

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Deploys</h1>
      {selected ? (
        <div className="card text-xs">
          <p className="font-semibold">Explorer view</p>
          <p>tx: {selected.txHash}</p>
          <pre className="max-h-48 overflow-auto rounded bg-slate-950 p-2">{JSON.stringify(selected.receipt, null, 2)}</pre>
        </div>
      ) : null}
      <div className="grid gap-2">
        {deploys.map((d) => (
          <a key={d.id} className="card block" href={d.txHash ? `/app/deploys?tx=${d.txHash}` : "#"}>
            <div className="flex items-center justify-between text-sm">
              <span className="truncate">{d.txHash ?? d.id}</span>
              <span className="rounded bg-slate-800 px-2 py-1 text-xs">{d.status}</span>
            </div>
            {d.txHash ? <p className="mt-1 text-xs text-slate-400">{env.EXPLORER_TX_URL.replace("{hash}", d.txHash)}</p> : null}
          </a>
        ))}
      </div>
    </div>
  );
}
