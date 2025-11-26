export class JsonRpcHttpClient {
  constructor(private baseUrl: string, private opts: { headers?: Record<string, string> } = {}) {}

  async request<T = unknown>(method: string, params?: unknown): Promise<T> {
    const payload = {
      jsonrpc: "2.0",
      id: Date.now(),
      method,
      params: params ?? [],
    };

    const res = await fetch(this.baseUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        ...(this.opts.headers ?? {}),
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(`RPC HTTP error ${res.status}`);
    }

    const json = await res.json();
    if (json?.error) {
      throw new Error(json.error.message || "RPC error");
    }
    return json?.result as T;
  }
}
