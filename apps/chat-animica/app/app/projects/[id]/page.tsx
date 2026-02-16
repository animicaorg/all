import { prisma } from "@/src/server/db/prisma";

export default async function ProjectDetailPage({ params }: { params: { id: string } }) {
  const project = await prisma.project.findUnique({ where: { id: params.id }, include: { threads: true, contracts: true } });
  if (!project) return <div className="card">Project not found</div>;

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">{project.name}</h1>
      <div className="card">Threads: {project.threads.length}</div>
      <div className="card">Contracts: {project.contracts.length}</div>
    </div>
  );
}
