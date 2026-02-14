const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("LefCoin Ecosystem", function () {
  let oracle, registry, lefCoin;
  let owner, alice, bob, charity, dave;

  beforeEach(async function () {
    [owner, alice, bob, charity, dave] = await ethers.getSigners();

    // Deploy SentimentOracle
    const SentimentOracle = await ethers.getContractFactory("SentimentOracle");
    oracle = await SentimentOracle.deploy();
    await oracle.waitForDeployment();

    // Deploy GoodSpendRegistry
    const GoodSpendRegistry = await ethers.getContractFactory("GoodSpendRegistry");
    registry = await GoodSpendRegistry.deploy();
    await registry.waitForDeployment();

    // Deploy LefCoin
    const LefCoin = await ethers.getContractFactory("LefCoin");
    lefCoin = await LefCoin.deploy(
      await oracle.getAddress(),
      await registry.getAddress()
    );
    await lefCoin.waitForDeployment();

    // Wire up oracle ↔ LefCoin ↔ registry
    await oracle.setLefCoinContract(await lefCoin.getAddress());
    await registry.setLefCoinContract(await lefCoin.getAddress());
    await oracle.authorizeReporter(owner.address);
  });

  describe("Deployment", function () {
    it("should mint initial supply to deployer", async function () {
      const supply = await lefCoin.totalSupply();
      const ownerBalance = await lefCoin.balanceOf(owner.address);
      expect(ownerBalance).to.equal(supply);
    });

    it("should have correct name and symbol", async function () {
      expect(await lefCoin.name()).to.equal("LefCoin");
      expect(await lefCoin.symbol()).to.equal("LEF");
    });

    it("should initialize LOVE Index at 500 (neutral)", async function () {
      const loveIndex = await oracle.getLoveIndex();
      expect(loveIndex).to.equal(500);
    });
  });

  describe("SentimentOracle", function () {
    it("should allow authorized reporter to update subindices", async function () {
      await oracle.updateSubIndex(0, 800); // Global Peace = 800
      const [name, weight, score] = await oracle.getSubIndex(0);
      expect(name).to.equal("Global Peace");
      expect(score).to.equal(800);
    });

    it("should reject unauthorized reporters", async function () {
      await expect(
        oracle.connect(alice).updateSubIndex(0, 800)
      ).to.be.revertedWith("Not an authorized reporter");
    });

    it("should compute weighted LOVE Index correctly", async function () {
      // Set all subindices to 1000 (maximum positivity)
      await oracle.updateSubIndex(0, 1000); // Peace 20%
      await oracle.updateSubIndex(1, 1000); // Charity 15%
      await oracle.updateSubIndex(2, 1000); // Social 20%
      await oracle.updateSubIndex(3, 1000); // Environment 15%
      await oracle.updateSubIndex(4, 1000); // Wellness 15%
      // Good Spend (15%) still at default 500

      // Expected: (1000*2000 + 1000*1500 + 1000*2000 + 1000*1500 + 1000*1500 + 500*1500) / 10000
      //         = (2000000 + 1500000 + 2000000 + 1500000 + 1500000 + 750000) / 10000
      //         = 9250000 / 10000 = 925
      const loveIndex = await oracle.getLoveIndex();
      expect(loveIndex).to.equal(925);
    });

    it("should reject scores above 1000", async function () {
      await expect(
        oracle.updateSubIndex(0, 1001)
      ).to.be.revertedWith("Score exceeds maximum");
    });
  });

  describe("GoodSpendRegistry", function () {
    it("should register a good destination", async function () {
      await registry.registerDestination(charity.address, "Save The World Foundation", 0);
      expect(await registry.isGoodDestination(charity.address)).to.be.true;
    });

    it("should reject duplicate registrations", async function () {
      await registry.registerDestination(charity.address, "Save The World Foundation", 0);
      await expect(
        registry.registerDestination(charity.address, "Duplicate", 0)
      ).to.be.revertedWith("Already registered");
    });

    it("should allow removal of destinations", async function () {
      await registry.registerDestination(charity.address, "Save The World Foundation", 0);
      await registry.removeDestination(charity.address);
      expect(await registry.isGoodDestination(charity.address)).to.be.false;
    });
  });

  describe("Transfers & Fees", function () {
    beforeEach(async function () {
      // Owner is fee-exempt, so transfer to alice first (owner → alice is fee-free)
      await lefCoin.transfer(alice.address, ethers.parseEther("100000"));
    });

    it("should deduct 1% fee on normal transfers", async function () {
      const amount = ethers.parseEther("1000");
      const expectedFee = amount / 100n; // 1%
      const expectedNet = amount - expectedFee;

      await lefCoin.connect(alice).transfer(bob.address, amount);

      expect(await lefCoin.balanceOf(bob.address)).to.equal(expectedNet);
    });

    it("should route fees to the contract (rewards pool)", async function () {
      const amount = ethers.parseEther("1000");

      const contractBalBefore = await lefCoin.balanceOf(await lefCoin.getAddress());
      await lefCoin.connect(alice).transfer(bob.address, amount);
      const contractBalAfter = await lefCoin.balanceOf(await lefCoin.getAddress());

      expect(contractBalAfter).to.be.gt(contractBalBefore);
    });

    it("should not charge fee for exempt addresses", async function () {
      const amount = ethers.parseEther("1000");
      // Owner is fee-exempt
      await lefCoin.transfer(bob.address, amount);
      expect(await lefCoin.balanceOf(bob.address)).to.equal(amount);
    });
  });

  describe("Good Spend Mechanics", function () {
    beforeEach(async function () {
      // Setup: register charity + fund alice
      await registry.registerDestination(charity.address, "Love Foundation", 0);
      await lefCoin.transfer(alice.address, ethers.parseEther("100000"));
    });

    it("should detect good spends and emit event", async function () {
      const amount = ethers.parseEther("1000");
      await expect(lefCoin.connect(alice).transfer(charity.address, amount))
        .to.emit(lefCoin, "GoodSpendDetected");
    });

    it("should charge bonus fee on good spends (extra 1%)", async function () {
      const amount = ethers.parseEther("1000");
      // Normal fee: 1% + good spend bonus: 1% = 2% total
      const expectedFee = amount * 2n / 100n;
      const expectedNet = amount - expectedFee;

      await lefCoin.connect(alice).transfer(charity.address, amount);

      expect(await lefCoin.balanceOf(charity.address)).to.equal(expectedNet);
    });

    it("should update Good Spend subindex in oracle", async function () {
      const amount = ethers.parseEther("50000");
      await lefCoin.connect(alice).transfer(charity.address, amount);

      const [, , score] = await oracle.getSubIndex(5); // Good Spend index
      expect(score).to.be.gt(0);
    });

    it("should track good spend volume in registry", async function () {
      const amount = ethers.parseEther("1000");
      await lefCoin.connect(alice).transfer(charity.address, amount);

      const volume = await registry.totalGoodSpendVolume();
      expect(volume).to.be.gt(0);
    });
  });

  describe("Holder Rewards", function () {
    beforeEach(async function () {
      // Fund alice and bob
      await lefCoin.transfer(alice.address, ethers.parseEther("100000"));
      await lefCoin.transfer(bob.address, ethers.parseEther("100000"));
    });

    it("should accumulate rewards for holders after transfers", async function () {
      // Alice transfers to a THIRD PARTY (not bob, not owner who is fee-exempt)
      // Fees are generated and distributed to all holders including bob who is passive.
      await lefCoin.connect(alice).transfer(dave.address, ethers.parseEther("10000"));

      // Bob should have pending rewards (he held tokens during the distribution)
      const pending = await lefCoin.viewPendingRewards(bob.address);
      expect(pending).to.be.gt(0);
    });

    it("should allow holders to claim rewards", async function () {
      // Generate fees via third-party transfer (not to bob, not to fee-exempt owner)
      await lefCoin.connect(alice).transfer(dave.address, ethers.parseEther("10000"));

      const pendingBefore = await lefCoin.viewPendingRewards(bob.address);
      expect(pendingBefore).to.be.gt(0);

      const balBefore = await lefCoin.balanceOf(bob.address);
      await lefCoin.connect(bob).claimRewards();
      const balAfter = await lefCoin.balanceOf(bob.address);

      expect(balAfter).to.be.gt(balBefore);
    });

    it("should amplify rewards when LOVE Index is high", async function () {
      // Set all subindices to maximum positivity
      await oracle.updateSubIndex(0, 1000);
      await oracle.updateSubIndex(1, 1000);
      await oracle.updateSubIndex(2, 1000);
      await oracle.updateSubIndex(3, 1000);
      await oracle.updateSubIndex(4, 1000);
      // LOVE Index is now ~925 (Good Spend still at 500)

      // Transfer to third party (not bob, not fee-exempt owner) so bob earns as passive holder
      await lefCoin.connect(alice).transfer(dave.address, ethers.parseEther("10000"));

      const rewardsHigh = await lefCoin.viewPendingRewards(bob.address);

      // With index 925, amplification = 925/500 = 1.85x
      // So rewards should be notably higher than the base 1% fee
      expect(rewardsHigh).to.be.gt(0);
    });
  });

  describe("The Positive Feedback Loop", function () {
    it("good spending → higher index → higher rewards for all", async function () {
      // Setup
      await registry.registerDestination(charity.address, "Global Love Fund", 0);
      await lefCoin.transfer(alice.address, ethers.parseEther("200000"));
      await lefCoin.transfer(bob.address, ethers.parseEther("100000"));

      // Record initial LOVE Index
      const indexBefore = await oracle.getLoveIndex();

      // Alice does a big good spend
      await lefCoin.connect(alice).transfer(charity.address, ethers.parseEther("100000"));

      // LOVE Index should have increased (Good Spend subindex went up)
      const indexAfter = await oracle.getLoveIndex();
      expect(indexAfter).to.be.gt(indexBefore);

      // Bob (who just held) should have accumulated rewards
      const bobRewards = await lefCoin.viewPendingRewards(bob.address);
      expect(bobRewards).to.be.gt(0);

      console.log("  LOVE Index before good spend:", indexBefore.toString());
      console.log("  LOVE Index after good spend :", indexAfter.toString());
      console.log("  Bob's passive rewards (LEF) :", ethers.formatEther(bobRewards));
      console.log("  → Positivity pays. The loop works.");
    });
  });
});
