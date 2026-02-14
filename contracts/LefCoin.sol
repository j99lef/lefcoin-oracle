// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

import "./SentimentOracle.sol";
import "./GoodSpendRegistry.sol";

/**
 * @title LefCoin (LEF)
 * @notice A sentiment-indexed token whose value is driven by love and positivity.
 *
 * Core mechanics:
 *   1. LOVE INDEX — A composite score (0–1000) computed from 6 world-sentiment
 *      subindices tracked by the SentimentOracle.
 *
 *   2. GOOD SPEND — When LEF is spent at verified good destinations (charities,
 *      sustainable businesses, etc.), it boosts the on-chain Good Spend subindex,
 *      raising the LOVE Index for everyone.
 *
 *   3. HOLDER REWARDS — A small fee on every transfer feeds a rewards pool.
 *      The reward rate is amplified by the LOVE Index: the more positive the
 *      world sentiment, the more holders earn. Positivity literally pays.
 *
 *   4. GOOD SPEND BONUS — Transfers to good destinations generate a bonus
 *      reward injection, further incentivizing prosocial spending.
 *
 * The result: a positive feedback loop where doing good → raises the index →
 * rewards all holders → incentivizes more good spending.
 */
contract LefCoin is ERC20, Ownable {

    SentimentOracle  public oracle;
    GoodSpendRegistry public registry;

    // ─── Token Parameters ────────────────────────────────────────────

    uint256 public constant INITIAL_SUPPLY = 1_000_000_000 * 1e18; // 1 billion LEF

    // Transfer fee: 1% (100 basis points) goes to rewards pool
    uint256 public transferFeeBps = 100;
    uint256 public constant MAX_FEE_BPS = 500; // cap at 5%

    // Bonus multiplier for good-spend transfers (extra 1% to rewards pool)
    uint256 public goodSpendBonusBps = 100;

    // ─── Rewards System ──────────────────────────────────────────────

    uint256 public rewardsPool;
    uint256 public totalRewardsDistributed;

    // Accumulated rewards per token (scaled by 1e36 for precision)
    uint256 public accRewardsPerToken;
    uint256 private constant PRECISION = 1e36;

    // Per-holder tracking
    mapping(address => uint256) public rewardDebt;
    mapping(address => uint256) public pendingRewards;

    // ─── Good Spend Tracking ─────────────────────────────────────────

    uint256 public goodSpendVolumeWindow;    // volume in current window
    uint256 public goodSpendWindowStart;
    uint256 public constant WINDOW_DURATION = 1 days;
    uint256 public goodSpendTarget = 100_000 * 1e18; // target volume per window

    // ─── Fee Exemptions ──────────────────────────────────────────────

    mapping(address => bool) public feeExempt;

    // ─── Events ──────────────────────────────────────────────────────

    event RewardsDistributed(uint256 amount, uint256 loveIndex);
    event RewardsClaimed(address indexed holder, uint256 amount);
    event GoodSpendDetected(address indexed from, address indexed to, uint256 amount);
    event FeeUpdated(uint256 newFeeBps);
    event GoodSpendBonusUpdated(uint256 newBonusBps);
    event GoodSpendTargetUpdated(uint256 newTarget);

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(
        address _oracle,
        address _registry
    ) ERC20("LefCoin", "LEF") Ownable(msg.sender) {
        oracle = SentimentOracle(_oracle);
        registry = GoodSpendRegistry(_registry);
        goodSpendWindowStart = block.timestamp;

        // Exempt owner and this contract from fees
        feeExempt[msg.sender] = true;
        feeExempt[address(this)] = true;

        // Mint initial supply to deployer
        _mint(msg.sender, INITIAL_SUPPLY);
    }

    // ─── Transfer Override ───────────────────────────────────────────

    function _update(
        address from,
        address to,
        uint256 amount
    ) internal override {
        // Mints and burns bypass fee logic
        if (from == address(0) || to == address(0)) {
            super._update(from, to, amount);
            return;
        }

        // Settle pending rewards for both parties before balance changes
        _settleRewards(from);
        _settleRewards(to);

        // Calculate fees
        uint256 fee = 0;
        bool isGoodSpend = registry.isGoodDestination(to);

        if (!feeExempt[from] && !feeExempt[to]) {
            fee = (amount * transferFeeBps) / 10000;

            if (isGoodSpend) {
                // Bonus injection for good spends
                uint256 bonus = (amount * goodSpendBonusBps) / 10000;
                fee += bonus;
            }
        }

        uint256 netAmount = amount - fee;

        // Execute the transfer
        super._update(from, to, netAmount);

        // Route fee to rewards pool
        if (fee > 0) {
            super._update(from, address(this), fee);
            _distributeToRewardsPool(fee);
        }

        // Track good spend
        if (isGoodSpend) {
            _recordGoodSpend(to, netAmount);
            emit GoodSpendDetected(from, to, netAmount);
        }

        // Update reward debts after balance changes
        rewardDebt[from] = (balanceOf(from) * accRewardsPerToken) / PRECISION;
        rewardDebt[to] = (balanceOf(to) * accRewardsPerToken) / PRECISION;
    }

    // ─── Rewards Distribution ────────────────────────────────────────

    function _distributeToRewardsPool(uint256 amount) internal {
        uint256 loveIndex = oracle.getLoveIndex();

        // Amplify rewards by the LOVE Index: at index 500 (neutral) = 1x,
        // at index 1000 (max positivity) = 2x, at index 0 = 0x.
        // This means positive sentiment literally multiplies holder rewards.
        uint256 amplifiedAmount = (amount * loveIndex) / 500;

        // The amplified portion beyond the base fee is minted as new rewards
        // (love creates abundance)
        uint256 bonusMint = 0;
        if (amplifiedAmount > amount) {
            bonusMint = amplifiedAmount - amount;
            _mint(address(this), bonusMint);
        }

        uint256 totalDistribution = amount + bonusMint;
        rewardsPool += totalDistribution;
        totalRewardsDistributed += totalDistribution;

        // Distribute across all holders (proportional to balance)
        uint256 circulatingSupply = totalSupply() - balanceOf(address(this));
        if (circulatingSupply > 0) {
            accRewardsPerToken += (totalDistribution * PRECISION) / circulatingSupply;
        }

        emit RewardsDistributed(totalDistribution, loveIndex);
    }

    function _settleRewards(address account) internal {
        if (account == address(this) || account == address(0)) return;

        uint256 owed = (balanceOf(account) * accRewardsPerToken) / PRECISION;
        uint256 debt = rewardDebt[account];

        if (owed > debt) {
            pendingRewards[account] += owed - debt;
        }
    }

    /**
     * @notice Claim accumulated rewards. Love comes back to those who hold.
     */
    function claimRewards() external {
        _settleRewards(msg.sender);
        uint256 reward = pendingRewards[msg.sender];
        require(reward > 0, "No rewards to claim");

        pendingRewards[msg.sender] = 0;
        rewardDebt[msg.sender] = (balanceOf(msg.sender) * accRewardsPerToken) / PRECISION;
        rewardsPool -= reward;

        // Transfer from contract's held rewards
        super._update(address(this), msg.sender, reward);

        emit RewardsClaimed(msg.sender, reward);
    }

    /**
     * @notice View pending rewards for an account.
     */
    function viewPendingRewards(address account) external view returns (uint256) {
        uint256 owed = (balanceOf(account) * accRewardsPerToken) / PRECISION;
        uint256 debt = rewardDebt[account];
        uint256 pending = pendingRewards[account];
        if (owed > debt) {
            pending += owed - debt;
        }
        return pending;
    }

    // ─── Good Spend Tracking ─────────────────────────────────────────

    function _recordGoodSpend(address dest, uint256 amount) internal {
        // Reset window if expired
        if (block.timestamp >= goodSpendWindowStart + WINDOW_DURATION) {
            goodSpendVolumeWindow = 0;
            goodSpendWindowStart = block.timestamp;
        }

        goodSpendVolumeWindow += amount;

        // Record in registry
        registry.recordGoodSpend(dest, amount);

        // Calculate Good Spend score (0–1000) based on progress toward target
        uint256 score = (goodSpendVolumeWindow * 1000) / goodSpendTarget;
        if (score > 1000) score = 1000;

        // Update the on-chain Good Spend subindex in the oracle
        oracle.updateGoodSpend(score);
    }

    // ─── Admin ───────────────────────────────────────────────────────

    function setTransferFee(uint256 newFeeBps) external onlyOwner {
        require(newFeeBps <= MAX_FEE_BPS, "Fee too high");
        transferFeeBps = newFeeBps;
        emit FeeUpdated(newFeeBps);
    }

    function setGoodSpendBonus(uint256 newBonusBps) external onlyOwner {
        require(newBonusBps <= MAX_FEE_BPS, "Bonus too high");
        goodSpendBonusBps = newBonusBps;
        emit GoodSpendBonusUpdated(newBonusBps);
    }

    function setGoodSpendTarget(uint256 newTarget) external onlyOwner {
        require(newTarget > 0, "Target must be positive");
        goodSpendTarget = newTarget;
        emit GoodSpendTargetUpdated(newTarget);
    }

    function setFeeExempt(address account, bool exempt) external onlyOwner {
        feeExempt[account] = exempt;
    }

    function setOracle(address _oracle) external onlyOwner {
        oracle = SentimentOracle(_oracle);
    }

    function setRegistry(address _registry) external onlyOwner {
        registry = GoodSpendRegistry(_registry);
    }

    // ─── Views ───────────────────────────────────────────────────────

    /**
     * @notice Get the current LOVE Index that drives holder rewards.
     */
    function getLoveIndex() external view returns (uint256) {
        return oracle.getLoveIndex();
    }
}
