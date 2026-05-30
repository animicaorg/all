import React, { useEffect, useState, useMemo } from 'react';

// CollectionTab renders the NFTs and ANM-20 tokens the active wallet
// owns. Backed by a scan of chain Transfer events filtered to the
// active address; results are cached in the extension's local storage
// keyed by wallet address so the UI shows immediately on reopen and
// refreshes in the background.
//
// Two sections:
//   - Tokens (ANM-20)  — balance, symbol, click to view in explorer
//   - NFTs (ANM-721)   — image, name, collection, click → marketplace
//
// All chain reads go via the extension's existing RPC client; no new
// permission requests are needed.

interface OwnedToken {
  contractAddress: string;
  name: string;
  symbol: string;
  decimals: number;
  balance: string;          // raw smallest-unit balance, decimal string
}

interface OwnedNft {
  contractAddress: string;
  collectionName: string;
  collectionSymbol: string;
  tokenId: string;
  name: string;
  imageUrl: string | null;
  marketplaceUrl: string;   // animica.xyz deep-link
}

interface CollectionTabProps {
  walletAddress: string;
}

const CACHE_NS = 'collection-cache:v1';
const MARKETPLACE_BASE = 'https://animica.xyz/marketplace';

function CollectionTab({ walletAddress }: CollectionTabProps) {
  const [tokens, setTokens] = useState<OwnedToken[]>([]);
  const [nfts, setNfts] = useState<OwnedNft[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  // Hydrate from cache immediately.
  useEffect(() => {
    if (!walletAddress) return;
    chrome.storage.local.get(`${CACHE_NS}:${walletAddress}`, (res) => {
      const cached = res[`${CACHE_NS}:${walletAddress}`];
      if (cached?.tokens) setTokens(cached.tokens);
      if (cached?.nfts) setNfts(cached.nfts);
      if (cached?.updatedAt) setLastUpdated(cached.updatedAt);
    });
    void refresh();
    const interval = setInterval(refresh, 60_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walletAddress]);

  async function refresh() {
    if (!walletAddress) return;
    setLoading(true);
    setError(null);
    try {
      const res = await chrome.runtime.sendMessage({
        method: 'wallet_listOwnedAssets',
        params: { address: walletAddress },
      });
      if (res?.error) throw new Error(res.error);
      const t = (res?.tokens ?? []) as OwnedToken[];
      const n = (res?.nfts ?? []) as OwnedNft[];
      setTokens(t);
      setNfts(n);
      const now = Date.now();
      setLastUpdated(now);
      chrome.storage.local.set({
        [`${CACHE_NS}:${walletAddress}`]: { tokens: t, nfts: n, updatedAt: now },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const totalTokens = tokens.length;
  const totalNfts = nfts.length;
  const updatedAgo = useMemo(() => {
    if (!lastUpdated) return null;
    const s = Math.floor((Date.now() - lastUpdated) / 1000);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  }, [lastUpdated]);

  return (
    <div className="collection-tab">
      <header className="collection-header">
        <h3>Your collection</h3>
        <div className="collection-meta">
          {updatedAgo && (
            <span className="updated-ago">Updated {updatedAgo}</span>
          )}
          <button
            type="button"
            className="refresh-btn"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && <p className="collection-error">{error}</p>}

      <section className="collection-section">
        <h4>
          Tokens <span className="count">{totalTokens}</span>
        </h4>
        {totalTokens === 0 ? (
          <p className="empty">No ANM-20 tokens yet.</p>
        ) : (
          <ul className="token-list">
            {tokens.map((t) => (
              <li key={t.contractAddress} className="token-row">
                <div className="token-meta">
                  <span className="token-symbol">{t.symbol}</span>
                  <span className="token-name">{t.name}</span>
                </div>
                <span className="token-balance">
                  {formatBalance(t.balance, t.decimals)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="collection-section">
        <h4>
          NFTs <span className="count">{totalNfts}</span>
        </h4>
        {totalNfts === 0 ? (
          <p className="empty">
            No NFTs yet.{' '}
            <a href={`${MARKETPLACE_BASE}/founders`} target="_blank" rel="noreferrer">
              Mint the Founders Pass →
            </a>
          </p>
        ) : (
          <div className="nft-grid">
            {nfts.map((n) => (
              <a
                key={`${n.contractAddress}:${n.tokenId}`}
                href={n.marketplaceUrl}
                target="_blank"
                rel="noreferrer"
                className="nft-card"
              >
                <div
                  className="nft-thumb"
                  style={
                    n.imageUrl
                      ? { backgroundImage: `url(${n.imageUrl})` }
                      : undefined
                  }
                />
                <p className="nft-name">{n.name || `#${n.tokenId}`}</p>
                <p className="nft-collection">{n.collectionName}</p>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function formatBalance(raw: string, decimals: number): string {
  try {
    const big = BigInt(raw);
    const denom = BigInt(10) ** BigInt(decimals || 0);
    const whole = big / denom;
    const frac = (big % denom).toString().padStart(decimals, '0').slice(0, 4);
    return `${whole.toString()}${frac ? '.' + frac : ''}`;
  } catch {
    return raw;
  }
}

export default CollectionTab;
