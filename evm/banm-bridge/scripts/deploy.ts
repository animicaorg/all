import { ethers, network } from "hardhat";
import path from "node:path";
import { writeDeploymentFile, type Deployment } from "./_common";

async function main(): Promise<void> {
  const [deployer] = await ethers.getSigners();
  const adminAddress = process.env.EVM_ADMIN_ADDRESS || deployer.address;
  const admin = ethers.getAddress(adminAddress);
  const chainId = (await ethers.provider.getNetwork()).chainId;

  console.log(`Deploying BANM bridge contracts on ${network.name} (chainId=${chainId})`);
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Admin: ${admin}`);

  const tokenFactory = await ethers.getContractFactory("BANMToken");
  const token = await tokenFactory.deploy(admin);
  await token.waitForDeployment();

  const controllerFactory = await ethers.getContractFactory("BANMBridgeController");
  const controller = await controllerFactory.deploy(admin, await token.getAddress());
  await controller.waitForDeployment();

  const vaultFactory = await ethers.getContractFactory("BANMBridgeVault");
  const vault = await vaultFactory.deploy(admin, await token.getAddress(), await controller.getAddress());
  await vault.waitForDeployment();

  const routerFactory = await ethers.getContractFactory("BANMBridgeDepositRouter");
  const router = await routerFactory.deploy(admin, await token.getAddress(), await vault.getAddress());
  await router.waitForDeployment();

  const deployment: Deployment = {
    chainId: Number(chainId),
    network: network.name,
    BANMToken: await token.getAddress(),
    BANMBridgeController: await controller.getAddress(),
    BANMBridgeVault: await vault.getAddress(),
    BANMBridgeDepositRouter: await router.getAddress(),
    deployedAt: new Date().toISOString()
  };

  const outPath = path.join("evm", "banm-bridge", "deployments", `${network.name}.json`);
  await writeDeploymentFile(outPath, deployment);

  console.log("Deployment complete:");
  console.log(JSON.stringify(deployment, null, 2));
  console.log(`Saved deployment file: ${outPath}`);
  console.log("Run scripts/grantRoles.ts next.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
