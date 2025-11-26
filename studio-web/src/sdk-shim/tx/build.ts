import { sha3_256 } from "../utils/hash";

export type DeployArgs = {
  manifest: unknown;
  code: Uint8Array;
  from: string;
  chainId?: string | number;
  value?: bigint | number | string;
  nonce?: bigint;
};

export async function buildDeploy(args: DeployArgs) {
  const meta = {
    from: args.from,
    chainId: args.chainId ?? "", 
    value: args.value ?? 0,
    nonce: args.nonce ?? 0n,
  };
  const signBytes = sha3_256(args.code ?? new Uint8Array());
  return { tx: { kind: "deploy", meta, manifest: args.manifest, code: args.code }, signBytes };
}

export async function buildDeployTx(args: DeployArgs) {
  return buildDeploy(args);
}

export async function deploy(args: DeployArgs) {
  return buildDeploy(args);
}

export async function deployTx(args: DeployArgs) {
  return buildDeploy(args);
}

export async function estimateDeployGas(): Promise<bigint> {
  return 500_000n;
}
