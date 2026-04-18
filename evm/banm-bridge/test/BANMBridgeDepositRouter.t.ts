import { expect } from "chai";
import { ethers } from "hardhat";
import { deployBridgeFixture } from "./fixtures";

describe("BANMBridgeDepositRouter", () => {
  it("deposits user BANM with order id and emits event", async () => {
    const { router, token, user, vault } = await deployBridgeFixture();
    const orderId = ethers.keccak256(ethers.toUtf8Bytes("order-router"));
    const amount = ethers.parseEther("3");

    await (await token.connect(user).approve(await router.getAddress(), amount)).wait();
    await expect(router.connect(user).deposit(orderId, amount))
      .to.emit(router, "DepositRegistered")
      .withArgs(orderId, user.address, amount, await vault.getAddress());

    expect(await token.balanceOf(await vault.getAddress())).to.equal(amount);
    await expect(router.connect(user).deposit(orderId, amount)).to.be.revertedWith("order already deposited");
  });
});
