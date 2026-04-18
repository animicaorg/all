import { ethers } from "hardhat";
import { readDeploymentFile, resolveDeploymentPath } from "./_common";

async function main(): Promise<void> {
  const deploymentPath = await resolveDeploymentPath(process.env.DEPLOYMENT_FILE);
  const deployment = await readDeploymentFile(deploymentPath);
  const [signer] = await ethers.getSigners();
  const target = (process.env.PAUSE_TARGET || "all").toLowerCase();

  const token = await ethers.getContractAt("BANMToken", deployment.BANMToken, signer);
  const controller = await ethers.getContractAt("BANMBridgeController", deployment.BANMBridgeController, signer);
  const vault = await ethers.getContractAt("BANMBridgeVault", deployment.BANMBridgeVault, signer);
  const router = await ethers.getContractAt("BANMBridgeDepositRouter", deployment.BANMBridgeDepositRouter, signer);

  if (target === "all" || target === "token") await (await token.unpause()).wait();
  if (target === "all" || target === "controller") await (await controller.unpause()).wait();
  if (target === "all" || target === "vault") await (await vault.unpause()).wait();
  if (target === "all" || target === "router") await (await router.unpause()).wait();

  console.log(`Unpaused target: ${target}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
