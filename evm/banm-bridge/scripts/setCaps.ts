import { ethers } from "hardhat";
import { readDeploymentFile, resolveDeploymentPath } from "./_common";

async function main(): Promise<void> {
  const deploymentPath = await resolveDeploymentPath(process.env.DEPLOYMENT_FILE);
  const deployment = await readDeploymentFile(deploymentPath);
  const [signer] = await ethers.getSigners();

  const dailyMintCap = process.env.BANM_DAILY_MINT_CAP || "0";
  const dailyReleaseCap = process.env.BANM_DAILY_RELEASE_CAP || "0";
  const controller = await ethers.getContractAt("BANMBridgeController", deployment.BANMBridgeController, signer);
  const tx = await controller.setCaps(dailyMintCap, dailyReleaseCap);
  await tx.wait();

  console.log("Updated caps", { dailyMintCap, dailyReleaseCap });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
