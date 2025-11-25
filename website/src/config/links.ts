/**
 * Computed deep links to first-party properties (Studio / Explorer / Docs).
 * All base URLs come from typed ENV (see src/env.ts).
 *
 * Usage:
 *   import { Links } from '@/config/links';
 *   const txUrl = Links.explorer.tx('0xabc...');
 *   const addrUrl = Links.explorer.address('anim1...');
 *   const studioSim = Links.studio.simulateCall('0xcontract...', 'increment', { by: 1 });
 *   const docsSdkPy = Links.docs.sdk('python');
 */

import { ENV } from '../env';

type Dict = Record<string, string | number | boolean | undefined | null>;

function withQuery(url: string, params?: Dict): string {
  if (!params) return url;
  const u = new URL(url);
  const sp = u.searchParams;
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    sp.set(k, String(v));
  }
  u.search = sp.toString();
  return u.toString();
}

function join(base: string, path: string = '', params?: Dict): string {
  // Normalize: remove extra slashes to avoid '//' when concatenating
  const b = base.replace(/\/+$/, '');
  const p = path.replace(/^\/+/, '');
  const url = p ? `${b}/${p}` : b;
  return withQuery(url, params);
}

function toHexOrRaw(x: string | number | bigint): string {
  if (typeof x === 'number' || typeof x === 'bigint') {
    const n = BigInt(x);
    return '0x' + n.toString(16);
  }
  return x;
}

export const Links = {
  chainId: ENV.CHAIN_ID,
  rpc: ENV.RPC_URL,

  studio: {
    root: ENV.STUDIO_URL,

    /** Open Studio landing or project picker */
    home(): string {
      return join(ENV.STUDIO_URL, '/');
    },

    /** Open a quickstart template, e.g., "counter" or "escrow" */
    template(name: 'counter' | 'escrow' | string): string {
      return join(ENV.STUDIO_URL, '/templates/open', { t: name });
    },

    /** Simulate a read/write call against a locally compiled contract */
    simulateCall(address: string, method: string, args?: Dict): string {
      return join(ENV.STUDIO_URL, '/simulate/call', { address, method, ...args });
    },

    /** Open deploy wizard with a prefilled manifest URL (hosted JSON) */
    deploy(manifestUrl?: string): string {
      return join(ENV.STUDIO_URL, '/deploy', manifestUrl ? { manifest: manifestUrl } : undefined);
    },

    /** Link to verify source for a deployed address */
    verify(address: string): string {
      return join(ENV.STUDIO_URL, '/verify', { address });
    },
  },

  explorer: {
    root: ENV.EXPLORER_URL,

    home(): string {
      return join(ENV.EXPLORER_URL, '/');
    },

    /** Address (supports bech32m or 0x hex) */
    address(addr: string): string {
      return join(ENV.EXPLORER_URL, `/address/${encodeURIComponent(addr)}`);
    },

    /** Transaction by hash */
    tx(hash: string): string {
      return join(ENV.EXPLORER_URL, `/tx/${encodeURIComponent(hash)}`);
    },

    /** Block by number or hash */
    block(id: number | string | bigint): string {
      const norm = typeof id === 'string' ? id : toHexOrRaw(id);
      return join(ENV.EXPLORER_URL, `/block/${encodeURIComponent(norm)}`);
    },

    /** Contract view (alias of address) with optional tab (code|events|state) */
    contract(addr: string, tab?: 'overview' | 'code' | 'events' | 'state'): string {
      return join(ENV.EXPLORER_URL, `/contract/${encodeURIComponent(addr)}`, tab ? { tab } : undefined);
    },

    /** Token view (for fungible or NFT contracts) */
    token(addr: string, tab?: 'holders' | 'transfers' | 'inventory'): string {
      return join(ENV.EXPLORER_URL, `/token/${encodeURIComponent(addr)}`, tab ? { tab } : undefined);
    },

    /** Search box deep link */
    search(q: string): string {
      return join(ENV.EXPLORER_URL, '/search', { q });
    },
  },

  docs: {
    root: ENV.DOCS_URL,

    home(): string {
      return join(ENV.DOCS_URL, '/');
    },

    /** Quick jump to Getting Started */
    gettingStarted(): string {
      return join(ENV.DOCS_URL, '/getting-started');
    },

    /** SDK section by language */
    sdk(lang: 'python' | 'typescript' | 'rust'): string {
      return join(ENV.DOCS_URL, `/sdks/${lang}`);
    },

    /** Concept or spec page by slug */
    page(slug: string): string {
      return join(ENV.DOCS_URL, `/${slug.replace(/^\/+/, '')}`);
    },
  },
};

export type LinksType = typeof Links;
