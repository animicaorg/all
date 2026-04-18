import { expect } from "chai";
import { ethers } from "hardhat";
import { deployBridgeFixture } from "./fixtures";

describe("BANMBridgeController", () => {
  it("mints once per order and emits mint events", async () => {
    const { controller, operator, token, user } = await deployBridgeFixture();
    const orderId = ethers.keccak256(ethers.toUtf8Bytes("order-a"));
    const amount = ethers.parseEther("2");

    await expect(
      controller
        .connect(operator)
        .executeMint(orderId, user.address, amount, 0n, "order-a")
    )
      .to.emit(controller, "MintExecuted")
      .withArgs(orderId, user.address, amount, 0n, "order-a");

    expect(await token.balanceOf(user.address)).to.equal(ethers.parseEther("102"));
    await expect(
      controller
        .connect(operator)
        .executeMint(orderId, user.address, amount, 0n, "order-a")
    ).to.be.revertedWith("mint already executed for order");
  });

  it("enforces mint caps", async () => {
    const { controller, operator, user } = await deployBridgeFixture();
    await (await controller.setCaps(ethers.parseEther("1"), ethers.parseEther("1000"))).wait();
    await expect(
      controller
        .connect(operator)
        .executeMint(ethers.keccak256(ethers.toUtf8Bytes("order-cap")), user.address, ethers.parseEther("2"), 0n, "cap")
    ).to.be.revertedWith("daily mint cap exceeded");
  });
});
