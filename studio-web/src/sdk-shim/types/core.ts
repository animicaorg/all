export type Hex = `0x${string}`;

export interface Tx {
  hash?: Hex;
  from?: string;
  to?: string | null;
  nonce?: number;
  gasLimit?: number;
  gasPrice?: string;
  value?: string;
  data?: Hex;
}

export interface Receipt {
  transactionHash?: Hex;
  status?: string | number;
  gasUsed?: string | number;
  blockHash?: Hex;
}

export interface Block {
  number?: number;
  hash?: Hex;
  parentHash?: Hex;
}

export interface Head extends Block {
  timestamp?: number;
}
