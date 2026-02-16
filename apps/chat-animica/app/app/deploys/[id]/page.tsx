import { prisma } from "@/src/server/db/prisma";

export default async function DeployDetailPage({ params }: { params: { id: string } }) {
  const deploy = await prisma.deployJob.findUnique({ where: { id: params.id } });
  if (!deploy) return <div className="card">Deploy not found</div>;

  const explorer = process.env.EXPLORER_TX_URL?.replace("{hash}", deploy.txHash ?? "");
  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Deploy {deploy.id}</h1>
      <pre className="card overflow-auto text-xs">{JSON.stringify(deploy, null, 2)}</pre>
      {explorer ? <a className="text-blue-400 underline" href={explorer}>View on explorer</a> : null}
    </div>
  );
}
