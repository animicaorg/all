import { expect } from "chai";
import { ethers } from "hardhat";
import { deployBridgeFixture } from "./fixtures";

describe("BANMBridgeVault", () => {
  it("registers and burns order deposits exactly once", async () => {
    const { vault, router, token, user, operator } = await deployBridgeFixture();
    const orderId = ethers.keccak256(ethers.toUtf8Bytes("order-vault"));
    const amount = ethers.parseEther("4");

    await (await token.connect(user).approve(await router.getAddress(), amount)).wait();
    await (await router.connect(user).deposit(orderId, amount)).wait();

    const before = await token.totalSupply();
    await expect(vault.connect(operator).burnForOrder(orderId)).to.emit(vault, "BurnExecuted");
    expect(await token.totalSupply()).to.equal(before - amount);
    await expect(vault.connect(operator).burnForOrder(orderId)).to.be.revertedWith("order already burned");
  });
});
