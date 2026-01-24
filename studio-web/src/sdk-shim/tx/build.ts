import { sha3_256 } from "../utils/hash";

export type DeployArgs = {
  manifest: unknown;
  code: Uint8Array;
  from: string;
  chainId?: string | number;
  value?: bigint | number | string;
  nonce?: bigint;
};

export type CallArgs = {
  to: string;
  from: string;
  chainId?: string | number;
  method: string;
  args?: unknown[];
  gasLimit?: bigint | number | string;
  maxFee?: bigint | number | string;
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

export async function buildCall(args: CallArgs) {
  const payload = JSON.stringify({
    to: args.to,
    from: args.from,
    method: args.method,
    args: args.args ?? [],
    gasLimit: args.gasLimit ?? null,
    maxFee: args.maxFee ?? null,
    value: args.value ?? null,
    nonce: args.nonce ?? null,
  });
  const signBytes = sha3_256(payload);
  return { tx: { kind: "call", to: args.to, from: args.from, data: payload }, signBytes };
}

export async function buildCallTx(args: CallArgs) {
  return buildCall(args);
}

export async function callTx(args: CallArgs) {
  return buildCall(args);
}

export async function call(args: CallArgs) {
  return buildCall(args);
}

export async function estimateCallGas(): Promise<bigint> {
  return 120_000n;
}
