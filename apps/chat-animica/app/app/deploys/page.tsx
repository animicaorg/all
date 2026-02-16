import Link from "next/link";
import { prisma } from "@/src/server/db/prisma";

export default async function DeploysPage() {
  const deploys = await prisma.deployJob.findMany({ orderBy: { createdAt: "desc" }, take: 25 });
  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Deploys</h1>
      <div className="grid gap-2">
        {deploys.map((d) => (
          <Link key={d.id} className="card block" href={`/app/deploys/${d.id}`}>
            <div className="flex items-center justify-between text-sm">
              <span className="truncate">{d.id}</span>
              <span className="rounded bg-slate-800 px-2 py-1 text-xs">{d.status}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
