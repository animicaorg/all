import crypto from "crypto";
import { env } from "@/src/server/env";

export type ConnectRequestPayload = {
  v: 1;
  app: "Animica Studio";
  origin: string;
  nonce: string;
  ts: number;
  callback: string;
  scopes: Array<"accounts" | "signTx">;
  chainId: number;
};

export type ConnectCallbackPayload = {
  requestId: string;
  approved: boolean;
  accounts?: string[];
  sessionPublicKey?: string;
  sessionToken?: string;
  nonceSignature: string;
};

export function makeNonce(size = 18) {
  return crypto.randomBytes(size).toString("hex");
}

export function signConnectPayload(payload: ConnectRequestPayload) {
  const body = JSON.stringify(payload);
  return crypto.createHmac("sha256", env.WALLET_CONNECT_SIGNING_KEY).update(body).digest("hex");
}

export function verifyConnectPayload(payload: ConnectRequestPayload, signature: string) {
  const expected = signConnectPayload(payload);
  if (expected.length !== signature.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}

export function signNonce(nonce: string) {
  return crypto.createHmac("sha256", env.WALLET_CONNECT_SIGNING_KEY).update(nonce).digest("hex");
}

export function verifyNonceSignature(nonce: string, signature: string) {
  const expected = signNonce(nonce);
  if (expected.length !== signature.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}

export function encodeRequest(payload: ConnectRequestPayload, signature: string) {
  return Buffer.from(JSON.stringify({ payload, signature })).toString("base64url");
}
