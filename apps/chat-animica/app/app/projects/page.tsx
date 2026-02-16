import Link from "next/link";
import { prisma } from "@/src/server/db/prisma";

export default async function ProjectsPage() {
  const projects = await prisma.project.findMany({ orderBy: { createdAt: "desc" }, take: 20 });
  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Projects</h1>
      <div className="grid gap-2">
        {projects.map((p) => (
          <Link key={p.id} className="card block" href={`/app/projects/${p.id}`}>{p.name}</Link>
        ))}
      </div>
    </div>
  );
}
