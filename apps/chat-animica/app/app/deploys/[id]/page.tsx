import { prisma } from "@/src/server/db/prisma";

function buildTroubleshootingPrompt(deploy: any) {
  if (!deploy.error && !deploy.rpcAttempts) return null;
  return [
    "Troubleshoot this Animica deployment failure:",
    `Deploy ID: ${deploy.id}`,
    `Error: ${JSON.stringify(deploy.error ?? {})}`,
    `RPC Attempts: ${JSON.stringify(deploy.rpcAttempts ?? [], null, 2)}`,
    "Suggest concrete tx fixes and safer retry parameters."
  ].join("\n");
}

export default async function DeployDetailPage({ params }: { params: { id: string } }) {
  const deploy = await prisma.deployJob.findUnique({ where: { id: params.id } });
  if (!deploy) return <div className="card">Deploy not found</div>;

  const explorer = process.env.EXPLORER_TX_URL?.replace("{hash}", deploy.txHash ?? "");
  const troubleshootingPrompt = buildTroubleshootingPrompt(deploy);

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Deploy {deploy.id}</h1>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="card text-sm">Status: {deploy.status}</div>
        <div className="card text-sm">Updated: {new Date(deploy.updatedAt).toLocaleString()}</div>
      </div>
      <pre className="card overflow-auto text-xs">{JSON.stringify(deploy, null, 2)}</pre>
      {troubleshootingPrompt ? <pre className="card overflow-auto text-xs">{troubleshootingPrompt}</pre> : null}
      {explorer ? <a className="text-blue-400 underline" href={explorer}>View on explorer</a> : null}
    </div>
  );
}
