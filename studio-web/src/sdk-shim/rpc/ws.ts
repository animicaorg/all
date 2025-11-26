type Pending = { resolve: (v: any) => void; reject: (e: any) => void };

export class JsonRpcWsClient {
  private ws: WebSocket;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private subscriptions = new Map<string, (data: any) => void>();

  constructor(private url: string) {
    this.ws = new WebSocket(url);
    this.ws.addEventListener("message", (ev) => this.onMessage(ev));
  }

  private onMessage(ev: MessageEvent) {
    try {
      const msg = JSON.parse(ev.data as string);
      if (msg?.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)!;
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message || "RPC error"));
        else resolve(msg.result);
        return;
      }
      if (msg?.method && this.subscriptions.has(msg.method)) {
        this.subscriptions.get(msg.method)?.(msg.params?.result ?? msg.params);
      }
    } catch (err) {
      console.warn("WS message parse failed", err);
    }
  }

  async request<T = unknown>(method: string, params?: unknown): Promise<T> {
    const id = this.nextId++;
    const payload = { jsonrpc: "2.0", id, method, params: params ?? [] };
    const data = JSON.stringify(payload);
    await this.waitOpen();
    const p = new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(data);
    });
    return p;
  }

  async subscribe<T = unknown>(method: string, params: unknown, onMessage: (data: T) => void): Promise<() => void> {
    await this.waitOpen();
    this.subscriptions.set(method, onMessage as any);
    this.ws.send(JSON.stringify({ jsonrpc: "2.0", id: this.nextId++, method, params: params ?? {} }));
    return () => this.subscriptions.delete(method);
  }

  close() {
    try {
      this.ws.close();
    } catch {/* noop */}
  }

  private async waitOpen(): Promise<void> {
    if (this.ws.readyState === WebSocket.OPEN) return;
    if (this.ws.readyState === WebSocket.CONNECTING) {
      await new Promise<void>((resolve, reject) => {
        const onOpen = () => {
          this.ws.removeEventListener("open", onOpen);
          this.ws.removeEventListener("error", onErr);
          resolve();
        };
        const onErr = (ev: Event) => {
          this.ws.removeEventListener("open", onOpen);
          this.ws.removeEventListener("error", onErr);
          reject(ev);
        };
        this.ws.addEventListener("open", onOpen);
        this.ws.addEventListener("error", onErr);
      });
      return;
    }
    this.ws = new WebSocket(this.url);
    this.ws.addEventListener("message", (ev) => this.onMessage(ev));
    await this.waitOpen();
  }
}
