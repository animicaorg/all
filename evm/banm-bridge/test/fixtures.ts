import { ethers } from "hardhat";

export async function deployBridgeFixture() {
  const [admin, operator, user, other] = await ethers.getSigners();

  const tokenFactory = await ethers.getContractFactory("BANMToken");
  const token = await tokenFactory.deploy(admin.address);
  await token.waitForDeployment();

  const controllerFactory = await ethers.getContractFactory("BANMBridgeController");
  const controller = await controllerFactory.deploy(admin.address, await token.getAddress());
  await controller.waitForDeployment();

  const vaultFactory = await ethers.getContractFactory("BANMBridgeVault");
  const vault = await vaultFactory.deploy(admin.address, await token.getAddress(), await controller.getAddress());
  await vault.waitForDeployment();

  const routerFactory = await ethers.getContractFactory("BANMBridgeDepositRouter");
  const router = await routerFactory.deploy(admin.address, await token.getAddress(), await vault.getAddress());
  await router.waitForDeployment();

  await (await token.grantRole(await token.MINTER_ROLE(), await controller.getAddress())).wait();
  await (await token.grantRole(await token.MINTER_ROLE(), admin.address)).wait();
  await (await token.grantRole(await token.BURNER_ROLE(), await vault.getAddress())).wait();
  await (await controller.grantRole(await controller.OPERATOR_ROLE(), operator.address)).wait();
  await (await controller.grantRole(await controller.VAULT_MANAGER_ROLE(), await vault.getAddress())).wait();
  await (await vault.grantRole(await vault.ROUTER_ROLE(), await router.getAddress())).wait();
  await (await vault.grantRole(await vault.BURNER_ROLE(), operator.address)).wait();
  await (await token.mint(user.address, ethers.parseEther("100"), ethers.keccak256(ethers.toUtf8Bytes("seed")))).wait();

  return { admin, operator, user, other, token, controller, vault, router };
}
