/**
 * LefCoin Ecosystem Simulation
 * =============================
 * A full simulation of the LefCoin smart contracts running in pure JavaScript.
 * No Solidity compiler or blockchain needed — this demonstrates every mechanic:
 *   - Token transfers with fees
 *   - LOVE Index computation from 6 weighted subindices
 *   - Good Spend detection and on-chain index boost
 *   - Holder reward accumulation amplified by the LOVE Index
 *   - Reward claiming
 *   - Community governance voting
 *
 * Run: node simulation/run.js
 */

// ─── Simulated SentimentOracle ───────────────────────────────────

class SentimentOracle {
  constructor() {
    this.subIndices = [
      { name: "Global Peace",        weight: 2000, score: 500 },
      { name: "Charitable Giving",   weight: 1500, score: 500 },
      { name: "Social Sentiment",    weight: 2000, score: 500 },
      { name: "Environmental Care",  weight: 1500, score: 500 },
      { name: "Community Wellness",  weight: 1500, score: 500 },
      { name: "Good Spend",          weight: 1500, score: 500 },
    ];
    this.reporters = new Set();
  }

  authorizeReporter(addr) { this.reporters.add(addr); }

  updateSubIndex(id, score, reporter) {
    if (!this.reporters.has(reporter)) throw new Error("Not an authorized reporter");
    if (id >= 5) throw new Error("Use updateGoodSpend for index 5");
    if (score > 1000) throw new Error("Score exceeds maximum");
    this.subIndices[id].score = score;
    log(`  Oracle: ${this.subIndices[id].name} → ${score}/1000`);
  }

  updateGoodSpend(score) {
    this.subIndices[5].score = Math.min(1000, score);
  }

  getLoveIndex() {
    let composite = 0;
    for (const s of this.subIndices) composite += s.score * s.weight;
    return Math.floor(composite / 10000);
  }
}

// ─── Simulated GoodSpendRegistry ─────────────────────────────────

class GoodSpendRegistry {
  constructor() {
    this.destinations = new Map();
    this.totalVolume = 0n;
    this.totalTx = 0;
  }

  register(addr, name, category) {
    this.destinations.set(addr, { name, category, totalReceived: 0n });
    log(`  Registry: Registered "${name}" (${category}) at ${addr}`);
  }

  isGoodDestination(addr) {
    return this.destinations.has(addr);
  }

  recordGoodSpend(addr, amount) {
    const d = this.destinations.get(addr);
    d.totalReceived += amount;
    this.totalVolume += amount;
    this.totalTx++;
  }
}

// ─── Simulated LefCoin Token ─────────────────────────────────────

class LefCoin {
  constructor(oracle, registry) {
    this.oracle = oracle;
    this.registry = registry;
    this.balances = new Map();
    this.totalSupply = 0n;
    this.transferFeeBps = 100n;
    this.goodSpendBonusBps = 100n;
    this.feeExempt = new Set();

    // Rewards system
    this.rewardsPool = 0n;
    this.totalRewardsDistributed = 0n;
    this.accRewardsPerToken = 0n;
    this.rewardDebt = new Map();
    this.pendingRewards = new Map();

    // Good spend tracking
    this.goodSpendVolume = 0n;
    this.goodSpendTarget = 100000n * BigInt(1e18);

    this.PRECISION = BigInt(1e36);
  }

  _balanceOf(addr) { return this.balances.get(addr) || 0n; }
  _setBalance(addr, val) { this.balances.set(addr, val); }
  _getDebt(addr) { return this.rewardDebt.get(addr) || 0n; }
  _getPending(addr) { return this.pendingRewards.get(addr) || 0n; }

  mint(to, amount) {
    this._setBalance(to, this._balanceOf(to) + amount);
    this.totalSupply += amount;
    this.feeExempt.add(to);
    this.feeExempt.add("__contract__");
  }

  transfer(from, to, amount) {
    const bal = this._balanceOf(from);
    if (bal < amount) throw new Error(`Insufficient balance: ${from} has ${fmt(bal)}, needs ${fmt(amount)}`);

    // Settle rewards before balance changes
    this._settleRewards(from);
    this._settleRewards(to);

    // Calculate fees
    let fee = 0n;
    const isGoodSpend = this.registry.isGoodDestination(to);

    if (!this.feeExempt.has(from) && !this.feeExempt.has(to)) {
      fee = (amount * this.transferFeeBps) / 10000n;
      if (isGoodSpend) {
        fee += (amount * this.goodSpendBonusBps) / 10000n;
      }
    }

    const netAmount = amount - fee;

    // Execute transfer
    this._setBalance(from, this._balanceOf(from) - amount);
    this._setBalance(to, this._balanceOf(to) + netAmount);

    // Route fee to rewards
    if (fee > 0n) {
      this._setBalance("__contract__", this._balanceOf("__contract__") + fee);
      this._distributeToRewardsPool(fee);
    }

    // Track good spend
    if (isGoodSpend) {
      this._recordGoodSpend(to, netAmount);
    }

    // Update debts
    this.rewardDebt.set(from, (this._balanceOf(from) * this.accRewardsPerToken) / this.PRECISION);
    this.rewardDebt.set(to, (this._balanceOf(to) * this.accRewardsPerToken) / this.PRECISION);

    const feeStr = fee > 0n ? ` (fee: ${fmt(fee)} LEF${isGoodSpend ? " incl. good spend bonus" : ""})` : "";
    log(`  Transfer: ${from} → ${to}: ${fmt(netAmount)} LEF${feeStr}`);
  }

  _distributeToRewardsPool(amount) {
    const loveIndex = BigInt(this.oracle.getLoveIndex());
    const amplified = (amount * loveIndex) / 500n;
    let bonusMint = 0n;
    if (amplified > amount) {
      bonusMint = amplified - amount;
      this._setBalance("__contract__", this._balanceOf("__contract__") + bonusMint);
      this.totalSupply += bonusMint;
    }

    const total = amount + bonusMint;
    this.rewardsPool += total;
    this.totalRewardsDistributed += total;

    const circulating = this.totalSupply - this._balanceOf("__contract__");
    if (circulating > 0n) {
      this.accRewardsPerToken += (total * this.PRECISION) / circulating;
    }
  }

  _settleRewards(account) {
    if (account === "__contract__") return;
    const owed = (this._balanceOf(account) * this.accRewardsPerToken) / this.PRECISION;
    const debt = this._getDebt(account);
    if (owed > debt) {
      this.pendingRewards.set(account, this._getPending(account) + (owed - debt));
    }
  }

  viewPendingRewards(account) {
    const owed = (this._balanceOf(account) * this.accRewardsPerToken) / this.PRECISION;
    const debt = this._getDebt(account);
    let pending = this._getPending(account);
    if (owed > debt) pending += owed - debt;
    return pending;
  }

  claimRewards(account) {
    this._settleRewards(account);
    const reward = this._getPending(account);
    if (reward === 0n) throw new Error("No rewards to claim");

    this.pendingRewards.set(account, 0n);
    this.rewardDebt.set(account, (this._balanceOf(account) * this.accRewardsPerToken) / this.PRECISION);
    this.rewardsPool -= reward;

    this._setBalance("__contract__", this._balanceOf("__contract__") - reward);
    this._setBalance(account, this._balanceOf(account) + reward);

    log(`  Claimed: ${account} received ${fmt(reward)} LEF in rewards`);
    return reward;
  }

  _recordGoodSpend(dest, amount) {
    this.goodSpendVolume += amount;
    this.registry.recordGoodSpend(dest, amount);
    let score = Number((this.goodSpendVolume * 1000n) / this.goodSpendTarget);
    if (score > 1000) score = 1000;
    this.oracle.updateGoodSpend(score);
  }
}

// ─── Simulated LoveGovernance ────────────────────────────────────

class LoveGovernance {
  constructor(lefCoin, registry) {
    this.lefCoin = lefCoin;
    this.registry = registry;
    this.proposals = [];
    this.threshold = BigInt(10000) * BigInt(1e18);
    this.votes = new Map();
  }

  propose(proposer, destAddr, destName, category) {
    const balance = this.lefCoin._balanceOf(proposer);
    if (balance < this.threshold) throw new Error("Below proposal threshold");

    const id = this.proposals.length;
    this.proposals.push({
      proposer, destination: destAddr, name: destName, category,
      forVotes: 0n, againstVotes: 0n, executed: false,
    });
    this.votes.set(id, new Set());
    log(`  Governance: Proposal #${id} created by ${proposer} — add "${destName}"`);
    return id;
  }

  vote(proposalId, voter, support) {
    const p = this.proposals[proposalId];
    if (this.votes.get(proposalId).has(voter)) throw new Error("Already voted");
    const weight = this.lefCoin._balanceOf(voter);
    if (weight === 0n) throw new Error("No voting power");

    this.votes.get(proposalId).add(voter);
    if (support) p.forVotes += weight;
    else p.againstVotes += weight;

    log(`  Governance: ${voter} voted ${support ? "FOR" : "AGAINST"} #${proposalId} (weight: ${fmt(weight)} LEF)`);
  }

  execute(proposalId) {
    const p = this.proposals[proposalId];
    if (p.executed) throw new Error("Already executed");
    p.executed = true;

    const passed = p.forVotes > p.againstVotes;
    if (passed) {
      this.registry.register(p.destination, p.name, p.category);
    }
    log(`  Governance: Proposal #${proposalId} ${passed ? "PASSED ✓" : "FAILED ✗"} (${fmt(p.forVotes)} FOR / ${fmt(p.againstVotes)} AGAINST)`);
    return passed;
  }
}

// ─── Utilities ───────────────────────────────────────────────────

function fmt(wei) {
  const eth = Number(wei) / 1e18;
  if (eth >= 1000000) return (eth / 1000000).toFixed(2) + "M";
  if (eth >= 1000) return (eth / 1000).toFixed(2) + "K";
  return eth.toFixed(4);
}

function log(msg) { console.log(msg); }

function printLoveIndex(oracle) {
  console.log("\n  ┌─────────────────────────────────────────┐");
  console.log(`  │  ♥  LOVE INDEX: ${oracle.getLoveIndex().toString().padStart(4)} / 1000${" ".repeat(15)}│`);
  console.log("  ├─────────────────────────────────────────┤");
  for (const s of oracle.subIndices) {
    const bar = "█".repeat(Math.floor(s.score / 50)) + "░".repeat(20 - Math.floor(s.score / 50));
    const pct = ((s.weight / 10000) * 100).toFixed(0);
    console.log(`  │  ${s.name.padEnd(22)} ${s.score.toString().padStart(4)} ${bar} ${pct}% │`);
  }
  console.log("  └─────────────────────────────────────────┘");
}

function printBalances(token, accounts) {
  console.log("\n  Balances:");
  for (const [name, addr] of Object.entries(accounts)) {
    const bal = token._balanceOf(addr);
    const rewards = token.viewPendingRewards(addr);
    const rewardStr = rewards > 0n ? ` (+${fmt(rewards)} pending rewards)` : "";
    console.log(`    ${name.padEnd(20)} ${fmt(bal).padStart(12)} LEF${rewardStr}`);
  }
}

// ─── Main Simulation ─────────────────────────────────────────────

function main() {
  console.log("╔══════════════════════════════════════════════════════════╗");
  console.log("║         LefCoin Ecosystem — Live Simulation             ║");
  console.log("║   Indexed by Love. Driven by Positivity.                ║");
  console.log("╚══════════════════════════════════════════════════════════╝");

  // ── Deploy ─────────────────────────────────────────────────────
  console.log("\n═══ Phase 1: Deployment ═══════════════════════════════════\n");

  const oracle = new SentimentOracle();
  const registry = new GoodSpendRegistry();
  const token = new LefCoin(oracle, registry);
  const governance = new LoveGovernance(token, registry);

  oracle.authorizeReporter("deployer");

  const SUPPLY = BigInt(1e9) * BigInt(1e18);
  token.mint("deployer", SUPPLY);

  console.log("  Contracts deployed:");
  console.log("  • SentimentOracle  — 6 subindices initialized at 500");
  console.log("  • GoodSpendRegistry — empty, community will vote on destinations");
  console.log("  • LefCoin (LEF)    — 1,000,000,000 LEF minted to deployer");
  console.log("  • LoveGovernance   — proposal threshold: 10,000 LEF");

  printLoveIndex(oracle);

  const accounts = {
    "Deployer": "deployer",
    "Alice": "alice",
    "Bob": "bob",
    "Carol": "carol",
    "SaveTheOceans": "charity_oceans",
    "GreenEnergy Fund": "charity_green",
  };

  // ── Distribute tokens ──────────────────────────────────────────
  console.log("\n═══ Phase 2: Token Distribution ═══════════════════════════\n");

  token.transfer("deployer", "alice", BigInt(200000) * BigInt(1e18));
  token.transfer("deployer", "bob", BigInt(150000) * BigInt(1e18));
  token.transfer("deployer", "carol", BigInt(100000) * BigInt(1e18));

  printBalances(token, accounts);

  // ── Register Good Spend via Governance ─────────────────────────
  console.log("\n═══ Phase 3: Community Governance ═════════════════════════\n");
  console.log("  Alice proposes adding 'Save The Oceans Foundation'...");

  const prop1 = governance.propose("alice", "charity_oceans", "Save The Oceans Foundation", "Charity");

  console.log("\n  Voting period (simulated)...");
  governance.vote(prop1, "alice", true);
  governance.vote(prop1, "bob", true);
  governance.vote(prop1, "carol", true);

  console.log("\n  Executing proposal...");
  governance.execute(prop1);

  console.log("\n  Bob proposes adding 'Green Energy Fund'...");
  const prop2 = governance.propose("bob", "charity_green", "Green Energy Fund", "Sustainability");
  governance.vote(prop2, "alice", true);
  governance.vote(prop2, "bob", true);
  governance.vote(prop2, "carol", false);
  console.log("\n  Executing proposal...");
  governance.execute(prop2);

  // ── Normal transfers (generate fees → rewards) ─────────────────
  console.log("\n═══ Phase 4: Normal Trading Activity ══════════════════════\n");
  console.log("  Users trade — 1% fee feeds the rewards pool...\n");

  token.transfer("alice", "bob", BigInt(10000) * BigInt(1e18));
  token.transfer("bob", "carol", BigInt(5000) * BigInt(1e18));
  token.transfer("carol", "alice", BigInt(2000) * BigInt(1e18));
  token.transfer("alice", "bob", BigInt(8000) * BigInt(1e18));

  console.log(`\n  Rewards pool: ${fmt(token.rewardsPool)} LEF`);
  console.log(`  Total distributed: ${fmt(token.totalRewardsDistributed)} LEF`);
  console.log(`  Reward multiplier: ${(oracle.getLoveIndex() / 500).toFixed(2)}x (LOVE Index: ${oracle.getLoveIndex()})`);

  printBalances(token, accounts);

  // ── Good Spend: The Positive Feedback Loop ─────────────────────
  console.log("\n═══ Phase 5: The Positive Feedback Loop ═══════════════════\n");
  console.log("  Alice sends LEF to Save The Oceans (verified good destination)...\n");

  const indexBefore = oracle.getLoveIndex();
  token.transfer("alice", "charity_oceans", BigInt(50000) * BigInt(1e18));

  console.log(`\n  Good Spend detected! 2% total fee (1% base + 1% good spend bonus)`);
  console.log(`  → Good Spend subindex boosted by on-chain activity`);

  const indexAfter = oracle.getLoveIndex();
  console.log(`\n  LOVE Index: ${indexBefore} → ${indexAfter} (+${indexAfter - indexBefore})`);

  console.log("\n  Bob also donates to Green Energy Fund...\n");
  token.transfer("bob", "charity_green", BigInt(30000) * BigInt(1e18));

  const indexAfter2 = oracle.getLoveIndex();
  console.log(`\n  LOVE Index: ${indexAfter} → ${indexAfter2} (+${indexAfter2 - indexAfter})`);

  printLoveIndex(oracle);
  printBalances(token, accounts);

  // ── Oracle Reports (world gets more positive) ──────────────────
  console.log("\n═══ Phase 6: World Sentiment Improves ═════════════════════\n");
  console.log("  Lefbot oracle pipeline reports improving world sentiment...\n");

  oracle.updateSubIndex(0, 720, "deployer"); // Peace improving
  oracle.updateSubIndex(1, 680, "deployer"); // Charitable giving up
  oracle.updateSubIndex(2, 650, "deployer"); // Social mood brightening
  oracle.updateSubIndex(3, 600, "deployer"); // Environment getting better
  oracle.updateSubIndex(4, 710, "deployer"); // Community wellness rising

  printLoveIndex(oracle);

  console.log(`\n  Reward multiplier now: ${(oracle.getLoveIndex() / 500).toFixed(2)}x (was 1.00x)`);
  console.log("  All future fees generate MORE rewards for holders!\n");

  // More trading at higher multiplier
  console.log("  More trading at the higher multiplier...\n");
  token.transfer("alice", "bob", BigInt(15000) * BigInt(1e18));
  token.transfer("bob", "carol", BigInt(10000) * BigInt(1e18));

  // ── Claim Rewards ──────────────────────────────────────────────
  console.log("\n═══ Phase 7: Claiming Rewards ═════════════════════════════\n");
  console.log("  Holders claim their accumulated rewards...\n");

  for (const name of ["alice", "bob", "carol"]) {
    const pending = token.viewPendingRewards(name);
    if (pending > 0n) {
      token.claimRewards(name);
    }
  }

  // ── Final State ────────────────────────────────────────────────
  console.log("\n═══ Final State ═══════════════════════════════════════════");

  printLoveIndex(oracle);
  printBalances(token, accounts);

  console.log(`\n  Ecosystem Stats:`);
  console.log(`    Total Supply:           ${fmt(token.totalSupply)} LEF`);
  console.log(`    Total Rewards Paid:     ${fmt(token.totalRewardsDistributed)} LEF`);
  console.log(`    Good Spend Volume:      ${fmt(token.goodSpendVolume)} LEF`);
  console.log(`    Good Spend Transactions: ${registry.totalTx}`);
  console.log(`    Registered Destinations: ${registry.destinations.size}`);
  console.log(`    Governance Proposals:    ${governance.proposals.length}`);

  console.log("\n╔══════════════════════════════════════════════════════════╗");
  console.log("║  The loop works:                                        ║");
  console.log("║    Good spending → Higher index → Bigger rewards → More ║");
  console.log("║    incentive to spend on good things → Repeat           ║");
  console.log("║                                                         ║");
  console.log("║  Positivity literally pays.                              ║");
  console.log("╚══════════════════════════════════════════════════════════╝\n");
}

main();
