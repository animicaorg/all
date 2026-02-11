// Provider injected into page context (window.animica)

interface AnimicaProvider {
  isAnimica: boolean;
  
  // Methods
  request(args: { method: string; params?: any[] }): Promise<any>;
  
  // Convenience methods
  animica_requestAccounts(): Promise<string[]>;
  animica_accounts(): Promise<string[]>;
  animica_chainId(): Promise<number>;
  animica_switchChain(chainId: number): Promise<void>;
  animica_signMessage(message: string): Promise<string>;
  animica_sendTransaction(tx: any): Promise<string>;
  
  // Event handling
  on(event: string, handler: (...args: any[]) => void): void;
  removeListener(event: string, handler: (...args: any[]) => void): void;
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

  async request(args: { method: string; params?: any[] }): Promise<any> {
    const id = ++this.requestId;
    
    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve, reject });
      
      window.postMessage({
        type: 'ANIMICA_PROVIDER_REQUEST',
        id,
        method: args.method,
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
    return this.request({ method: 'provider_requestAccounts' });
  }

  async animica_accounts(): Promise<string[]> {
    return this.request({ method: 'provider_getAccounts' });
  }

  async animica_chainId(): Promise<number> {
    return this.request({ method: 'provider_getChainId' });
  }

  async animica_switchChain(chainId: number): Promise<void> {
    return this.request({ method: 'wallet_switchNetwork', params: [{ networkId: chainId.toString() }] });
  }

  async animica_signMessage(message: string): Promise<string> {
    return this.request({ method: 'provider_signMessage', params: [{ message }] });
  }

  async animica_sendTransaction(tx: any): Promise<string> {
    return this.request({ method: 'provider_sendTransaction', params: [tx] });
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
