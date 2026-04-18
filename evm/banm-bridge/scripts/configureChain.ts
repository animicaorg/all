import { ethers } from "hardhat";
import { readDeploymentFile, resolveDeploymentPath } from "./_common";

async function main(): Promise<void> {
  const deploymentPath = await resolveDeploymentPath(process.env.DEPLOYMENT_FILE);
  const deployment = await readDeploymentFile(deploymentPath);
  const [signer] = await ethers.getSigners();

  const chainId = Number(process.env.TARGET_CHAIN_ID || deployment.chainId);
  const enabled = (process.env.TARGET_CHAIN_ENABLED || "true").toLowerCase() === "true";
  const router = ethers.getAddress(process.env.TARGET_CHAIN_ROUTER || deployment.BANMBridgeDepositRouter);
  const vault = ethers.getAddress(process.env.TARGET_CHAIN_VAULT || deployment.BANMBridgeVault);
  const label = process.env.TARGET_CHAIN_LABEL || "bnb";

  const controller = await ethers.getContractAt("BANMBridgeController", deployment.BANMBridgeController, signer);
  const tx = await controller.configureChain(chainId, enabled, router, vault, label);
  await tx.wait();

  console.log("Configured chain", { chainId, enabled, router, vault, label });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
