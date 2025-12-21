/**
 * Computed deep links to first-party properties.
 * Base URLs come from typed ENV (see src/env.ts).
 */

import { ENV } from '../env';

type Dict = Record<string, string | number | boolean | undefined | null>;

type ExplorerTab = 'overview' | 'code' | 'events' | 'state';
type TokenTab = 'holders' | 'transfers' | 'inventory';

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
  rpc: {
    url: ENV.RPC_URL,
    ws(): string {
      const u = new URL(ENV.RPC_URL);
      const proto = u.protocol === 'https:' ? 'wss:' : 'ws:';
      const path = u.pathname.endsWith('/rpc')
        ? u.pathname.replace(/\/rpc$/, '/ws')
        : u.pathname.endsWith('/')
        ? `${u.pathname}ws`
        : `${u.pathname}/ws`;
      return `${proto}//${u.host}${path}${u.search}`;
    },
  },

  explorer: {
    root: ENV.EXPLORER_URL,

    home(): string {
      return join(ENV.EXPLORER_URL, '/');
    },

    address(addr: string): string {
      return join(ENV.EXPLORER_URL, `/address/${encodeURIComponent(addr)}`);
    },

    tx(hash: string): string {
      return join(ENV.EXPLORER_URL, `/tx/${encodeURIComponent(hash)}`);
    },

    block(id: number | string | bigint): string {
      const norm = typeof id === 'string' ? id : toHexOrRaw(id);
      return join(ENV.EXPLORER_URL, `/block/${encodeURIComponent(norm)}`);
    },

    contract(addr: string, tab?: ExplorerTab): string {
      return join(ENV.EXPLORER_URL, `/contract/${encodeURIComponent(addr)}`, tab ? { tab } : undefined);
    },

    token(addr: string, tab?: TokenTab): string {
      return join(ENV.EXPLORER_URL, `/token/${encodeURIComponent(addr)}`, tab ? { tab } : undefined);
    },

    search(q: string): string {
      return join(ENV.EXPLORER_URL, '/search', { q });
    },
  },

  explorer2: ENV.EXPLORER2_URL
    ? {
        root: ENV.EXPLORER2_URL,
        home(): string {
          return join(ENV.EXPLORER2_URL as string, '/');
        },
      }
    : undefined,

  docs: {
    root: ENV.DOCS_URL,

    home(): string {
      return join(ENV.DOCS_URL, '/');
    },

    gettingStarted(): string {
      return join(ENV.DOCS_URL, '/getting-started');
    },

    sdk(lang: 'python' | 'typescript' | 'rust'): string {
      return join(ENV.DOCS_URL, `/sdks/${lang}`);
    },

    page(slug: string): string {
      return join(ENV.DOCS_URL, `/${slug.replace(/^\/+/, '')}`);
    },
  },

  studio: {
    root: ENV.STUDIO_URL,
    home(): string {
      return join(ENV.STUDIO_URL, '/');
    },
    template(name: 'counter' | 'escrow' | string): string {
      return join(ENV.STUDIO_URL, '/templates/open', { t: name });
    },
    deploy(manifestUrl?: string): string {
      return join(ENV.STUDIO_URL, '/deploy', manifestUrl ? { manifest: manifestUrl } : undefined);
    },
  },

  github: {
    root: ENV.GITHUB_URL,
    releases(): string {
      return join(ENV.GITHUB_URL, '/releases/latest');
    },
    discussions(): string {
      return join(ENV.GITHUB_URL, '/discussions');
    },
    issues(): string {
      return join(ENV.GITHUB_URL, '/issues');
    },
  },

  faucet: {
    url: ENV.FAUCET_URL,
  },

  pool: {
    url: ENV.POOL_URL,
  },

  community: {
    discord: ENV.DISCORD_URL,
    telegram: ENV.TELEGRAM_URL,
    x: ENV.X_URL,
  },
};

export const links = Links;
export type LinksType = typeof Links;
