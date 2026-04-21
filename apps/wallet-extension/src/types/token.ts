export interface WatchedToken {
  type: string;
  address: string;
  symbol: string;
  decimals: number;
  chainId: number;
  name?: string;
  image?: string;
  addedAt: number;
  addedByOrigin?: string;
}
