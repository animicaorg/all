import { useCallback, useMemo, useRef, useState } from "react";
import type { Receipt } from "@animica/sdk/src/types/core"; // falls back to local types if needed
import { toHex, fromHex } from "../utils/bytes";
import * as RPC from "../services/rpc";
import { getProvider } from "../services/provider";
import {
  buildDeploy as sdkBuildDeploy,
  estimateDeployGas as sdkEstimateDeployGas,
} from "@animica/sdk/src/tx/build";
import {
  encodeSignBytes as sdkEncodeSignBytes,
  encodeSignedTx as sdkEncodeSignedTx,
} from "@animica/sdk/src/tx/encode";

/**
 * useDeploy — build, sign, send a deploy transaction and await its receipt.
 *
 * This hook is transport/provider-agnostic:
 *  - Builds deploy transactions with the TypeScript SDK (@animica/sdk)
 *  - Signs with a detected wallet provider (window.animica via services/provider)
 *  - Sends via RPC wrapper (services/rpc) and waits for the receipt (poll or WS)
 *
 * It prefers a single-call provider.signAndSend(signBytes) if available,
 * otherwise falls back to provider.sign(signBytes) → encodeSignedTx → RPC.sendRawTransaction.
 */

export type DeployStatus =
  | "idle"
  | "estimating"
  | "building"
  | "signing"
  | "sending"
  | "waiting"
  | "success"
  | "error";

export interface DeployInputs {
  /** Deployer address (hex or bech32 supported by chain rules). */
  from: string;
  /** Chain identifier for the transaction. */
  chainId: number | string;
  /** Contract manifest JSON (normalized as per toolchain). */
  manifest: unknown;
  /** Compiled contract code blob (e.g., WASM or VM bytecode). */
  code: Uint8Array;
  /** Optional salt to influence deterministic address (if chain supports CREATE2-like). */
  salt?: string;
  /** Optional token value to transfer on deploy. */
  value?: string | bigint;
  /** Optional explicit nonce. If omitted, RPC will be consulted when building. */
  nonce?: number;
  /** Optional explicit gas limit. If omitted, estimateDeployGas will be used. */
  gasLimit?: number;
  /** Optional max fee per gas (decimal string or bigint). */
  maxFeePerGas?: string | bigint;
  /** Optional arbitrary metadata or tags (not sent on-chain unless supported). */
  meta?: Record<string, unknown>;
}

export interface BuildResult {
  tx: any; // Tx object as defined by the SDK (kept as 'any' to avoid tight coupling in app)
  signBytes: Uint8Array; // CBOR-encoded signable payload
  gasLimit: number;
}

export interface DeployResult {
  txHash: string;
  receipt: Receipt;
}

export interface UseDeploy {
  status: DeployStatus;
  error: string | null;
  progress: string[];
  building: boolean;
  sending: boolean;
  /** Estimate gas for the given deploy inputs. */
  estimateGas: (inputs: Omit<DeployInputs, "gasLimit">) => Promise<number>;
  /** Build a deploy transaction & return sign-bytes. */
  build: (inputs: DeployInputs) => Promise<BuildResult>;
  /** Sign (via provider) and send the deploy; wait for the receipt. */
  signAndSend: (inputs: DeployInputs) => Promise<DeployResult>;
  /** Cancel any in-flight wait (receipt polling); safe to call anytime. */
  cancel: () => void;
  /** Reset status & errors. */
  reset: () => void;
}

/** Internal helper to stringify fee-like inputs into canonical decimal strings. */
function toDecimalString(v?: string | bigint): string | undefined {
  if (v === undefined) return undefined;
  return typeof v === "bigint" ? v.toString() : v;
}

export function useDeploy(): UseDeploy {
  const [status, setStatus] = useState<DeployStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string[]>([]);
  const cancelWaitRef = useRef<() => void>(() => {});

  const push = useCallback((msg: string) => {
    setProgress((p) => [...p, msg]);
  }, []);

  const reset = useCallback(() => {
    setStatus("idle");
    setError(null);
    setProgress([]);
  }, []);

  const cancel = useCallback(() => {
    try {
      cancelWaitRef.current?.();
    } catch {
      /* noop */
    }
  }, []);

  const estimateGas = useCallback(
    async (inputs: Omit<DeployInputs, "gasLimit">): Promise<number> => {
      setStatus("estimating");
      setError(null);
      try {
        // Delegate to SDK's estimator; SDK uses RPC under the hood.
        const est = await sdkEstimateDeployGas({
          from: inputs.from,
          chainId: inputs.chainId,
          manifest: inputs.manifest,
          code: inputs.code,
          salt: inputs.salt,
          value: toDecimalString(inputs.value),
          nonce: inputs.nonce,
        });
        setStatus("idle");
        return est;
      } catch (e: any) {
        setStatus("error");
        const msg = e?.message ?? "Failed to estimate gas";
        setError(msg);
        throw new Error(msg);
      }
    },
    []
  );

  const build = useCallback(
    async (inputs: DeployInputs): Promise<BuildResult> => {
      setStatus("building");
      setError(null);
      setProgress([]);

      try {
        // Ensure gasLimit — estimate if absent
        let gasLimit = inputs.gasLimit;
        if (!gasLimit || gasLimit <= 0) {
          push("Estimating gas…");
          gasLimit = await sdkEstimateDeployGas({
            from: inputs.from,
            chainId: inputs.chainId,
            manifest: inputs.manifest,
            code: inputs.code,
            salt: inputs.salt,
            value: toDecimalString(inputs.value),
            nonce: inputs.nonce,
          });
        }

        push("Assembling deploy transaction…");
        const tx = await sdkBuildDeploy({
          from: inputs.from,
          chainId: inputs.chainId,
          manifest: inputs.manifest,
          code: inputs.code,
          salt: inputs.salt,
          value: toDecimalString(inputs.value),
          nonce: inputs.nonce,
          gasLimit,
          maxFeePerGas: toDecimalString(inputs.maxFeePerGas),
          meta: inputs.meta,
        });

        push("Encoding sign-bytes (CBOR) …");
        const signBytes = sdkEncodeSignBytes(tx);

        setStatus("idle");
        return { tx, signBytes, gasLimit };
      } catch (e: any) {
        const msg = e?.message ?? "Failed to build deploy transaction";
        setStatus("error");
        setError(msg);
        push(msg);
        throw new Error(msg);
      }
    },
    [push]
  );

  const signAndSend = useCallback(
    async (inputs: DeployInputs): Promise<DeployResult> => {
      setStatus("signing");
      setError(null);
      setProgress([]);

      try {
        // 1) Build (includes estimation if needed)
        const { signBytes } = await build(inputs);

        // 2) Sign with provider (wallet)
        push("Locating wallet provider…");
        const provider = await getProvider();
        if (!provider) {
          throw new Error("No wallet provider available. Please install or connect Animica Wallet.");
        }

        let txHash: string | undefined;
        let rawSigned: Uint8Array | undefined;

        // Prefer unified sign+send if wallet supports it
        if (typeof (provider as any).signAndSend === "function") {
          push("Requesting wallet to sign & send…");
          txHash = await (provider as any).signAndSend(signBytes, {
            type: "cbor",
            from: inputs.from,
            chainId: inputs.chainId,
          });
          if (!txHash || typeof txHash !== "string") {
            throw new Error("Wallet did not return a transaction hash.");
          }
        } else {
          push("Requesting wallet signature…");
          // Flexible signatures: bytes or hex accepted
          let sigHex: string | undefined;
          let sigBytes: Uint8Array | undefined;

          if (typeof (provider as any).sign === "function") {
            const out = await (provider as any).sign(signBytes, {
              type: "cbor",
              from: inputs.from,
              chainId: inputs.chainId,
            });
            if (out instanceof Uint8Array) sigBytes = out;
            else if (typeof out === "string") sigHex = out;
          } else if (typeof (provider as any).request === "function") {
            // Generic request path
            const out = await (provider as any).request("animica_signBytes", {
              from: inputs.from,
              chainId: inputs.chainId,
              signBytes: toHex(signBytes),
            });
            if (out && typeof out === "string") sigHex = out;
          } else {
            throw new Error("Wallet does not support signing.");
          }

          if (!sigBytes && !sigHex) throw new Error("Wallet did not return a signature.");
          if (!sigBytes && sigHex) sigBytes = fromHex(sigHex);

          push("Encoding signed transaction…");
          rawSigned = sdkEncodeSignedTx({ signBytes, signature: sigBytes! });

          // 3) Send via RPC
          push("Broadcasting transaction…");
          txHash = await RPC.sendRawTransaction(rawSigned);
        }

        setStatus("waiting");
        push(`Waiting for receipt… (${txHash})`);

        // 4) Await receipt
        const { receipt, cancel } = await RPC.awaitReceipt(txHash!, {
          // Use defaults inside RPC.awaitReceipt: WS if available, else poll
          timeoutMs: 60_000,
          pollIntervalMs: 1_000,
          returnCancel: true,
        });
        cancelWaitRef.current = cancel;

        if (!receipt) {
          throw new Error("Transaction dropped or timed out without a receipt.");
        }

        setStatus("success");
        push("Deploy confirmed.");
        return { txHash: txHash!, receipt };
      } catch (e: any) {
        const msg = e?.message ?? "Failed to deploy contract";
        setStatus("error");
        setError(msg);
        push(msg);
        throw new Error(msg);
      }
    },
    [build, push]
  );

  const building = useMemo(() => status === "estimating" || status === "building" || status === "signing", [status]);
  const sending = useMemo(() => status === "sending" || status === "waiting", [status]);

  return {
    status,
    error,
    progress,
    building,
    sending,
    estimateGas,
    build,
    signAndSend,
    cancel,
    reset,
  };
}

export default useDeploy;

/* ===========================
   Integration expectations:

   ../services/provider.ts should export:
   - getProvider(): Promise<{
       signAndSend?(signBytes: Uint8Array, opts?: { type?: "cbor"; from?: string; chainId?: number | string }): Promise<string>;
       sign?(signBytes: Uint8Array, opts?: { type?: "cbor"; from?: string; chainId?: number | string }): Promise<Uint8Array | string>;
       request?(method: string, params: any): Promise<any>;
     } | null>

   ../services/rpc.ts should export:
   - sendRawTransaction(raw: Uint8Array | string): Promise<string>
   - awaitReceipt(txHash: string, opts?: {
       timeoutMs?: number;
       pollIntervalMs?: number;
       returnCancel?: boolean;
     }): Promise<{ receipt: Receipt | null; cancel: () => void }>

   The @animica/sdk exports used here come from the previously
   generated SDK modules:
   - src/tx/build.ts:   buildDeploy(), estimateDeployGas()
   - src/tx/encode.ts:  encodeSignBytes(), encodeSignedTx()
   =========================== */
