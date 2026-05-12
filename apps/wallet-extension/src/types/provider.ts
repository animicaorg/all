// Permission and provider types

export interface DappPermission {
  origin: string;
  accounts: string[];
  grantedAt: number;
  lastUsedAt: number;
}

export interface ProviderRequest {
  method: string;
  params?: any[];
}

export interface ProviderResponse {
  result?: any;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

export enum ProviderMethod {
  REQUEST_ACCOUNTS = 'animica_requestAccounts',
  ACCOUNTS = 'animica_accounts',
  CHAIN_ID = 'animica_chainId',
  SWITCH_CHAIN = 'animica_switchChain',
  SIGN_MESSAGE = 'animica_signMessage',
  SEND_TRANSACTION = 'animica_sendTransaction',
  GET_BALANCE = 'animica_getBalance',
  GET_NONCE = 'animica_getNonce',
  WATCH_ASSET = 'animica_watchAsset',
  ADD_TOKEN = 'animica_addToken',
}

export enum ProviderEvent {
  ACCOUNTS_CHANGED = 'accountsChanged',
  CHAIN_CHANGED = 'chainChanged',
  DISCONNECT = 'disconnect',
}
