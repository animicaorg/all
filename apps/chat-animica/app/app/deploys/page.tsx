import Link from "next/link";
import { prisma } from "@/src/server/db/prisma";

export default async function DeploysPage() {
  const deploys = await prisma.deployJob.findMany({ orderBy: { createdAt: "desc" }, take: 25 });
  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Deploys</h1>
      {deploys.map((d) => (
        <Link key={d.id} className="card block" href={`/app/deploys/${d.id}`}>{d.id} — {d.status}</Link>
      ))}
    </div>
  );
}
