import { BrowserProvider, Contract, ethers } from "ethers";

export type WalletState = {
  provider: BrowserProvider | null;
  account: string | null;
  chainId: number | null;
  isMetaMask: boolean;
};

type EthereumProvider = {
  isMetaMask?: boolean;
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  on?: (event: string, listener: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

export function getInjectedProvider(): EthereumProvider | null {
  if (typeof window === "undefined" || !window.ethereum) return null;
  return window.ethereum;
}

export async function connectWallet(): Promise<WalletState> {
  const injected = getInjectedProvider();
  if (!injected) {
    return {
      provider: null,
      account: null,
      chainId: null,
      isMetaMask: false
    };
  }

  await injected.request({ method: "eth_requestAccounts" });
  const provider = new BrowserProvider(window.ethereum as any);
  const signer = await provider.getSigner();
  const network = await provider.getNetwork();
  return {
    provider,
    account: await signer.getAddress(),
    chainId: Number(network.chainId),
    isMetaMask: Boolean(injected.isMetaMask)
  };
}

export async function signOrderTypedData(
  provider: BrowserProvider,
  typedData: {
    domain: Record<string, unknown>;
    types: Record<string, Array<{ name: string; type: string }>>;
    message: Record<string, unknown>;
    primaryType: string;
  }
): Promise<string> {
  const signer = await provider.getSigner();
  const { domain, message } = typedData;
  const types = { ...typedData.types };
  delete (types as Record<string, unknown>).EIP712Domain;
  return signer.signTypedData(domain, types, message);
}

export async function ensureBnbChain(provider: BrowserProvider, chainId: number): Promise<void> {
  const hexChain = `0x${chainId.toString(16)}`;
  try {
    await provider.send("wallet_switchEthereumChain", [{ chainId: hexChain }]);
    return;
  } catch {
    if (chainId === 97) {
      await provider.send("wallet_addEthereumChain", [
        {
          chainId: "0x61",
          chainName: "BNB Smart Chain Testnet",
          nativeCurrency: { name: "tBNB", symbol: "tBNB", decimals: 18 },
          rpcUrls: ["https://data-seed-prebsc-1-s1.binance.org:8545"],
          blockExplorerUrls: ["https://testnet.bscscan.com"]
        }
      ]);
      return;
    }
    if (chainId === 56) {
      await provider.send("wallet_addEthereumChain", [
        {
          chainId: "0x38",
          chainName: "BNB Smart Chain",
          nativeCurrency: { name: "BNB", symbol: "BNB", decimals: 18 },
          rpcUrls: ["https://bsc-dataseed.binance.org"],
          blockExplorerUrls: ["https://bscscan.com"]
        }
      ]);
      return;
    }
    throw new Error(`Unsupported chain ID for auto-add: ${chainId}`);
  }
}

const routerAbi = ["function deposit(bytes32 orderId, uint256 amount) external"];

const tokenAbi = [
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function allowance(address owner, address spender) external view returns (uint256)"
];

function orderIdToBytes32(orderId: string): string {
  if (orderId.startsWith("0x") && orderId.length === 66) return orderId;
  return ethers.id(orderId);
}

export async function submitRouterDeposit(
  provider: BrowserProvider,
  routerAddress: string,
  tokenAddress: string,
  orderId: string,
  amountWei: bigint
): Promise<{ approveTxHash: string | null; depositTxHash: string }> {
  const signer = await provider.getSigner();
  const signerAddress = await signer.getAddress();
  const token = new Contract(tokenAddress, tokenAbi, signer);
  const router = new Contract(routerAddress, routerAbi, signer);
  const allowance = (await token.allowance(signerAddress, routerAddress)) as bigint;
  let approveTxHash: string | null = null;
  if (allowance < amountWei) {
    const approveTx = await token.approve(routerAddress, amountWei);
    const approveReceipt = await approveTx.wait();
    approveTxHash = approveReceipt?.hash || approveTx.hash;
  }
  const depositTx = await router.deposit(orderIdToBytes32(orderId), amountWei);
  const depositReceipt = await depositTx.wait();
  return {
    approveTxHash,
    depositTxHash: depositReceipt?.hash || depositTx.hash
  };
}
