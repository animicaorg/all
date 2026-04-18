import { ethers } from "hardhat";
import { readDeploymentFile, resolveDeploymentPath } from "./_common";

async function main(): Promise<void> {
  const deploymentPath = await resolveDeploymentPath(process.env.DEPLOYMENT_FILE);
  const deployment = await readDeploymentFile(deploymentPath);

  const [deployer] = await ethers.getSigners();
  const operator = ethers.getAddress(process.env.EVM_OPERATOR_ADDRESS || deployer.address);
  const pauser = ethers.getAddress(process.env.EVM_PAUSER_ADDRESS || deployer.address);
  const burner = ethers.getAddress(process.env.EVM_BURNER_ADDRESS || operator);

  const token = await ethers.getContractAt("BANMToken", deployment.BANMToken, deployer);
  const controller = await ethers.getContractAt("BANMBridgeController", deployment.BANMBridgeController, deployer);
  const vault = await ethers.getContractAt("BANMBridgeVault", deployment.BANMBridgeVault, deployer);
  const router = await ethers.getContractAt("BANMBridgeDepositRouter", deployment.BANMBridgeDepositRouter, deployer);

  const ops: Array<Promise<unknown>> = [];

  ops.push(token.grantRole(await token.MINTER_ROLE(), deployment.BANMBridgeController));
  ops.push(token.grantRole(await token.BURNER_ROLE(), deployment.BANMBridgeVault));

  ops.push(controller.grantRole(await controller.OPERATOR_ROLE(), operator));
  ops.push(controller.grantRole(await controller.PAUSER_ROLE(), pauser));
  ops.push(controller.grantRole(await controller.VAULT_MANAGER_ROLE(), deployment.BANMBridgeVault));

  ops.push(vault.grantRole(await vault.ROUTER_ROLE(), deployment.BANMBridgeDepositRouter));
  ops.push(vault.grantRole(await vault.BURNER_ROLE(), burner));
  ops.push(vault.grantRole(await vault.PAUSER_ROLE(), pauser));

  ops.push(router.grantRole(await router.PAUSER_ROLE(), pauser));
  ops.push(token.grantRole(await token.PAUSER_ROLE(), pauser));

  for (const tx of ops) {
    await (await tx).wait();
  }

  console.log("Roles granted successfully.");
  console.log({
    operator,
    pauser,
    burner
  });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
