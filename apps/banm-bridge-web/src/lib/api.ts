import axios from "axios";

export const API_BASE = import.meta.env.VITE_BANM_BRIDGE_API_BASE_URL || "http://localhost:8660";

const client = axios.create({
  baseURL: API_BASE
});

export type BridgeDirection = "ANM_TO_BANM" | "BANM_TO_ANM";

export type CreateOrderPayload = {
  direction: BridgeDirection;
  connected_evm_address: string;
  amount: string;
  source_address?: string;
  destination_address?: string;
  source_chain?: string;
  destination_chain?: string;
  chain_id?: number;
};

export async function createOrder(payload: CreateOrderPayload) {
  const response = await client.post("/api/v1/orders", payload);
  return response.data;
}

export async function verifySignature(orderId: string, signature: string, signatureType = "EIP712") {
  const response = await client.post(`/api/v1/orders/${orderId}/signature/verify`, {
    signature,
    signature_type: signatureType
  });
  return response.data;
}

export async function attachAnimicaDeposit(orderId: string, txHash: string) {
  const response = await client.post(`/api/v1/orders/${orderId}/deposits/animica`, { tx_hash: txHash });
  return response.data;
}

export async function attachEvmDeposit(orderId: string, txHash: string) {
  const response = await client.post(`/api/v1/orders/${orderId}/deposits/evm`, { tx_hash: txHash });
  return response.data;
}

export async function getOrderStatus(orderId: string) {
  const response = await client.get(`/api/v1/orders/${orderId}`);
  return response.data;
}

export async function getPublicSolvency() {
  const response = await client.get("/api/v1/solvency/public");
  return response.data;
}

export async function confirmClaimCode(orderId: string, claimCode: string) {
  const response = await client.post(`/api/v1/orders/${orderId}/claim-code/confirm`, {
    claim_code: claimCode
  });
  return response.data;
}
