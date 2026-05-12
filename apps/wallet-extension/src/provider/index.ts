// Provider injected into page context (window.animica)

type RequestParams = any[] | Record<string, unknown> | undefined;

interface AnimicaProvider {
  isAnimica: boolean;

  // Methods
  request(args: { method: string; params?: RequestParams }): Promise<any>;

  // Convenience methods
  animica_requestAccounts(): Promise<string[]>;
  animica_accounts(): Promise<string[]>;
  animica_chainId(): Promise<number>;
  animica_switchChain(chainId: number | string): Promise<void>;
  animica_signMessage(message: string): Promise<string>;
  animica_sendTransaction(tx: any): Promise<string>;
  animica_getBalance(address: string): Promise<string>;
  animica_getNonce(address: string): Promise<number>;
  animica_watchAsset(payload: any): Promise<boolean>;
  animica_addToken(token: any): Promise<boolean>;

  // Event handling
  on(event: string, handler: (...args: any[]) => void): void;
  removeListener(event: string, handler: (...args: any[]) => void): void;
}

const METHOD_ALIASES: Record<string, string> = {
  eth_requestAccounts: 'animica_requestAccounts',
  eth_accounts: 'animica_accounts',
  eth_sendTransaction: 'animica_sendTransaction',
};

function toHexChainId(chainId: number | string | bigint): string {
  if (typeof chainId === 'number' && Number.isFinite(chainId)) {
    return `0x${Math.trunc(chainId).toString(16)}`;
  }

  if (typeof chainId === 'bigint') {
    return `0x${chainId.toString(16)}`;
  }

  if (typeof chainId === 'string') {
    const trimmed = chainId.trim();
    if (/^0x[0-9a-f]+$/i.test(trimmed)) return trimmed.toLowerCase();
    if (/^\d+$/.test(trimmed)) return `0x${BigInt(trimmed).toString(16)}`;
  }

  throw new Error('Invalid chainId');
}

function normalizeRequestMethod(method: string): string {
  return METHOD_ALIASES[method] || method;
}

class AnimicaProviderImpl implements AnimicaProvider {
  isAnimica = true;
  private requestId = 0;
  private pendingRequests = new Map<number, { resolve: Function; reject: Function }>();
  private eventHandlers = new Map<string, Set<Function>>();

  constructor() {
    window.addEventListener('message', (event) => {
      if (event.source !== window) return;
      if (event.data.type !== 'ANIMICA_PROVIDER_RESPONSE') return;

      const { id, result, error } = event.data;
      const pending = this.pendingRequests.get(id);

      if (pending) {
        this.pendingRequests.delete(id);

        if (error) {
          pending.reject(new Error(error.message || 'Request failed'));
        } else {
          pending.resolve(result);
        }
      }
    });
  }

  async request(args: { method: string; params?: RequestParams }): Promise<any> {
    const id = ++this.requestId;
    const method = normalizeRequestMethod(args.method);

    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve, reject });

      window.postMessage({
        type: 'ANIMICA_PROVIDER_REQUEST',
        id,
        method,
        params: args.params || [],
      }, '*');

      // Timeout after 60 seconds
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error('Request timeout'));
        }
      }, 60000);
    });
  }

  async animica_requestAccounts(): Promise<string[]> {
    return this.request({ method: 'animica_requestAccounts' });
  }

  async animica_accounts(): Promise<string[]> {
    return this.request({ method: 'animica_accounts' });
  }

  async animica_chainId(): Promise<number> {
    return this.request({ method: 'animica_chainId' });
  }

  async animica_switchChain(chainId: number | string): Promise<void> {
    return this.request({
      method: 'animica_switchChain',
      params: [{ chainId: toHexChainId(chainId) }],
    });
  }

  async animica_signMessage(message: string): Promise<string> {
    return this.request({ method: 'provider_signMessage', params: [{ message }] });
  }

  async animica_sendTransaction(tx: any): Promise<string> {
    return this.request({ method: 'animica_sendTransaction', params: [tx] });
  }

  async animica_getBalance(address: string): Promise<string> {
    return this.request({ method: 'animica_getBalance', params: [address] });
  }

  async animica_getNonce(address: string): Promise<number> {
    return this.request({ method: 'animica_getNonce', params: [address] });
  }

  async animica_watchAsset(payload: any): Promise<boolean> {
    return this.request({ method: 'animica_watchAsset', params: [payload] });
  }

  async animica_addToken(token: any): Promise<boolean> {
    return this.request({ method: 'animica_addToken', params: [token] });
  }

  on(event: string, handler: (...args: any[]) => void): void {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, new Set());
    }
    this.eventHandlers.get(event)!.add(handler);
  }

  removeListener(event: string, handler: (...args: any[]) => void): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.delete(handler);
    }
  }

  emit(event: string, ...args: any[]): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.forEach(handler => {
        try {
          handler(...args);
        } catch (error) {
          console.error('Event handler error:', error);
        }
      });
    }
  }
}

// Inject provider into window
(window as any).animica = new AnimicaProviderImpl();

// Announce provider
window.dispatchEvent(new Event('animica#initialized'));
