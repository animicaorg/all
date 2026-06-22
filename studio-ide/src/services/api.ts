// Same-origin API client. The broker (studio-host) serves this SPA and the
// /api/ide/* routes on the same host, so cookies (anm_sid) flow automatically.

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(message: string, status: number, data?: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function parse(res: Response): Promise<any> {
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }
  const text = await res.text();
  // A redirect to the login HTML page shows up as HTML — treat as auth error.
  if (text.trim().startsWith("<")) return { __html: true };
  return text;
}

async function request(method: string, path: string, body?: any): Promise<any> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await parse(res);
  if (!res.ok || (data && data.__html)) {
    const msg = (data && (data.error || data.message)) || `Request failed (${res.status})`;
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

export const api = {
  get: (p: string) => request("GET", p),
  post: (p: string, body?: any) => request("POST", p, body ?? {}),
  put: (p: string, body?: any) => request("PUT", p, body ?? {}),
  del: (p: string) => request("DELETE", p),
};
