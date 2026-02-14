const { ethers } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying LefCoin ecosystem with account:", deployer.address);
  console.log("Account balance:", (await ethers.provider.getBalance(deployer.address)).toString());

  // 1. Deploy SentimentOracle
  console.log("\n--- Deploying SentimentOracle ---");
  const SentimentOracle = await ethers.getContractFactory("SentimentOracle");
  const oracle = await SentimentOracle.deploy();
  await oracle.waitForDeployment();
  const oracleAddress = await oracle.getAddress();
  console.log("SentimentOracle deployed to:", oracleAddress);

  // 2. Deploy GoodSpendRegistry
  console.log("\n--- Deploying GoodSpendRegistry ---");
  const GoodSpendRegistry = await ethers.getContractFactory("GoodSpendRegistry");
  const registry = await GoodSpendRegistry.deploy();
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log("GoodSpendRegistry deployed to:", registryAddress);

  // 3. Deploy LefCoin
  console.log("\n--- Deploying LefCoin ---");
  const LefCoin = await ethers.getContractFactory("LefCoin");
  const lefCoin = await LefCoin.deploy(oracleAddress, registryAddress);
  await lefCoin.waitForDeployment();
  const lefCoinAddress = await lefCoin.getAddress();
  console.log("LefCoin deployed to:", lefCoinAddress);

  // 4. Deploy LoveGovernance
  console.log("\n--- Deploying LoveGovernance ---");
  const LoveGovernance = await ethers.getContractFactory("LoveGovernance");
  const governance = await LoveGovernance.deploy(lefCoinAddress, registryAddress);
  await governance.waitForDeployment();
  const governanceAddress = await governance.getAddress();
  console.log("LoveGovernance deployed to:", governanceAddress);

  // 5. Wire up contracts
  console.log("\n--- Wiring contracts ---");

  let tx;
  tx = await oracle.setLefCoinContract(lefCoinAddress);
  await tx.wait();
  console.log("Oracle ← LefCoin (Good Spend updates)");

  tx = await registry.setLefCoinContract(lefCoinAddress);
  await tx.wait();
  console.log("Registry ← LefCoin (Good Spend recording)");

  tx = await oracle.authorizeReporter(deployer.address);
  await tx.wait();
  console.log("Deployer authorized as oracle reporter");

  // Transfer registry ownership to governance (so community votes control it)
  tx = await registry.transferOwnership(governanceAddress);
  await tx.wait();
  console.log("Registry ownership → LoveGovernance (community-controlled)");

  // Summary
  console.log("\n========================================");
  console.log("  LefCoin Ecosystem Deployed!");
  console.log("========================================");
  console.log("  SentimentOracle  :", oracleAddress);
  console.log("  GoodSpendRegistry:", registryAddress);
  console.log("  LefCoin (LEF)    :", lefCoinAddress);
  console.log("  LoveGovernance   :", governanceAddress);
  console.log("  Initial Supply   : 1,000,000,000 LEF");
  console.log("  LOVE Index       :", (await oracle.getLoveIndex()).toString(), "/ 1000");
  console.log("========================================");
  console.log("\nNext steps:");
  console.log("  1. Update frontend/index.html with contract addresses");
  console.log("  2. Update oracle/.env with ORACLE_ADDRESS");
  console.log("  3. Run oracle: python oracle/sentiment_pipeline.py --daemon");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
