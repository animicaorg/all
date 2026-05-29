// Tiny fetch wrapper that always sends credentials and parses errors.
export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
    ...init,
  });
  let body: unknown = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    body = await res.json().catch(() => null);
  } else {
    body = await res.text().catch(() => '');
  }
  if (!res.ok) {
    throw new ApiError(
      (body as { error?: string })?.error || `HTTP ${res.status}`,
      res.status,
      body,
    );
  }
  return body as T;
}

export const api = {
  get: <T,>(p: string) => request<T>(p),
  post: <T,>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T,>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T,>(p: string) => request<T>(p, { method: 'DELETE' }),
};
