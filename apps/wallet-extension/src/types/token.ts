export interface WatchedToken {
  type: string;
  address: string;
  symbol: string;
  decimals: number;
  chainId: number;
  name?: string;
  image?: string;
  tokenId?: string;
  addedAt: number;
  addedByOrigin?: string;
}
