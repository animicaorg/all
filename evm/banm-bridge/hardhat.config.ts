import "@nomicfoundation/hardhat-toolbox";
import { config as loadEnv } from "dotenv";
import type { HardhatUserConfig } from "hardhat/config";

loadEnv();

function optionalPrivateKey(envName: string): string[] {
  const key = process.env[envName];
  if (!key || key.trim() === "") return [];
  return [key.trim()];
}

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200
      }
    }
  },
  networks: {
    hardhat: {},
    bscTestnet: {
      url: process.env.BSC_TESTNET_RPC_URL || "https://data-seed-prebsc-1-s1.binance.org:8545",
      chainId: 97,
      accounts: optionalPrivateKey("EVM_DEPLOYER_PRIVATE_KEY")
    },
    bscMainnet: {
      url: process.env.BSC_MAINNET_RPC_URL || "https://bsc-dataseed.binance.org",
      chainId: 56,
      accounts: optionalPrivateKey("EVM_DEPLOYER_PRIVATE_KEY")
    }
  },
  mocha: {
    timeout: 120000
  }
};

export default config;

