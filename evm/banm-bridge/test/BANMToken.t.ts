import { expect } from "chai";
import { ethers } from "hardhat";

describe("BANMToken", () => {
  it("starts with zero supply and enforces minter role", async () => {
    const [admin, user] = await ethers.getSigners();
    const token = await (await ethers.getContractFactory("BANMToken")).deploy(admin.address);
    await token.waitForDeployment();

    expect(await token.totalSupply()).to.equal(0n);
    await expect(token.connect(user).mint(user.address, 1n, ethers.ZeroHash)).to.be.reverted;
  });

  it("supports pause/unpause and burner role", async () => {
    const [admin, user] = await ethers.getSigners();
    const token = await (await ethers.getContractFactory("BANMToken")).deploy(admin.address);
    await token.waitForDeployment();

    await (await token.grantRole(await token.MINTER_ROLE(), admin.address)).wait();
    await (await token.grantRole(await token.BURNER_ROLE(), user.address)).wait();
    await (await token.mint(user.address, ethers.parseEther("1"), ethers.ZeroHash)).wait();

    await (await token.pause()).wait();
    await expect(token.connect(user).transfer(admin.address, 1n)).to.be.reverted;
    await (await token.unpause()).wait();

    await (await token.connect(user).bridgeBurn(1000n, ethers.ZeroHash)).wait();
    expect(await token.totalSupply()).to.equal(ethers.parseEther("1") - 1000n);
  });
});

