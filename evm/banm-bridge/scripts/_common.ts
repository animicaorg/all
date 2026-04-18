import { promises as fs } from "node:fs";
import path from "node:path";
import { ethers, network } from "hardhat";

export type Deployment = {
  chainId: number;
  network: string;
  BANMToken: string;
  BANMBridgeController: string;
  BANMBridgeVault: string;
  BANMBridgeDepositRouter: string;
  deployedAt: string;
};

export function getRole(name: string): string {
  return ethers.keccak256(ethers.toUtf8Bytes(name));
}

export async function readDeploymentFile(filePath: string): Promise<Deployment> {
  const raw = await fs.readFile(filePath, "utf8");
  return JSON.parse(raw) as Deployment;
}

export async function writeDeploymentFile(filePath: string, deployment: Deployment): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(deployment, null, 2));
}

export async function resolveDeploymentPath(flagPath?: string): Promise<string> {
  if (flagPath) return flagPath;
  return path.join("evm", "banm-bridge", "deployments", `${network.name}.json`);
}

