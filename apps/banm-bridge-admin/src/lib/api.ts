import axios from "axios";

export const ADMIN_API_BASE = import.meta.env.VITE_BANM_BRIDGE_API_BASE_URL || "http://localhost:8660";

const client = axios.create({
  baseURL: ADMIN_API_BASE
});

export async function login(username: string, password: string) {
  const response = await client.post("/api/v1/admin/login", { username, password });
  return response.data as { access_token: string; role: string };
}

export async function fetchOrders(token: string, params?: { status?: string; direction?: string; limit?: number }) {
  const response = await client.get("/api/v1/admin/orders", {
    params,
    headers: { Authorization: `Bearer ${token}` }
  });
  return response.data;
}

export async function fetchOrderDetail(token: string, orderId: string) {
  const response = await client.get(`/api/v1/admin/orders/${orderId}`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  return response.data;
}

export async function retryOrder(token: string, orderId: string) {
  const response = await client.post(
    `/api/v1/admin/orders/${orderId}/retry`,
    {},
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return response.data;
}

export async function markManualReview(token: string, orderId: string, reason: string) {
  const response = await client.post(
    `/api/v1/admin/orders/${orderId}/manual-review`,
    {},
    {
      params: { reason },
      headers: { Authorization: `Bearer ${token}` }
    }
  );
  return response.data;
}

export async function setPauseFlag(token: string, flagName: string, paused: boolean) {
  const response = await client.post(
    `/api/v1/admin/pause/${flagName}`,
    { paused },
    { headers: { Authorization: `Bearer ${token}` } }
  );
  return response.data;
}

export async function fetchAdminSolvency(token: string) {
  const response = await client.get("/api/v1/admin/solvency", {
    headers: { Authorization: `Bearer ${token}` }
  });
  return response.data;
}

