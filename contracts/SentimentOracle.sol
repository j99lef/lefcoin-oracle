// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title SentimentOracle
 * @notice Manages the LOVE Index — a composite score of world sentiment subindices.
 *         The index ranges from 0 to 1000 (basis points of maximum positivity).
 *
 * Subindices:
 *   0 - Global Peace        (20%) — armed conflicts, treaties, diplomacy signals
 *   1 - Charitable Giving   (15%) — global donation volumes
 *   2 - Social Sentiment    (20%) — NLP analysis of public discourse
 *   3 - Environmental Care  (15%) — emissions, reforestation, conservation
 *   4 - Community Wellness  (15%) — health, education, civic participation
 *   5 - Good Spend          (15%) — on-chain: LefCoin spent at verified good destinations
 *
 * The first five subindices are fed by authorized oracle reporters (off-chain data).
 * The Good Spend subindex is updated on-chain by the LefCoin contract itself.
 */
contract SentimentOracle is Ownable {

    uint256 public constant NUM_SUBINDICES = 6;
    uint256 public constant MAX_SCORE = 1000;

    // Subindex identifiers
    uint256 public constant GLOBAL_PEACE       = 0;
    uint256 public constant CHARITABLE_GIVING  = 1;
    uint256 public constant SOCIAL_SENTIMENT   = 2;
    uint256 public constant ENVIRONMENTAL_CARE = 3;
    uint256 public constant COMMUNITY_WELLNESS = 4;
    uint256 public constant GOOD_SPEND         = 5;

    struct SubIndex {
        string  name;
        uint256 weight;   // basis points (sum must be 10000)
        uint256 score;    // 0–1000
        uint256 updatedAt;
    }

    SubIndex[6] public subIndices;

    mapping(address => bool) public oracleReporters;
    address public lefCoinContract;

    event SubIndexUpdated(uint256 indexed id, uint256 newScore, address reporter);
    event ReporterAuthorized(address reporter);
    event ReporterRevoked(address reporter);
    event LoveIndexUpdated(uint256 compositeScore);

    modifier onlyReporter() {
        require(oracleReporters[msg.sender], "Not an authorized reporter");
        _;
    }

    modifier onlyLefCoin() {
        require(msg.sender == lefCoinContract, "Only LefCoin contract");
        _;
    }

    constructor() Ownable(msg.sender) {
        // Initialize subindices with names and weights (basis points, total = 10000)
        subIndices[GLOBAL_PEACE]       = SubIndex("Global Peace",        2000, 500, block.timestamp);
        subIndices[CHARITABLE_GIVING]  = SubIndex("Charitable Giving",   1500, 500, block.timestamp);
        subIndices[SOCIAL_SENTIMENT]   = SubIndex("Social Sentiment",    2000, 500, block.timestamp);
        subIndices[ENVIRONMENTAL_CARE] = SubIndex("Environmental Care",  1500, 500, block.timestamp);
        subIndices[COMMUNITY_WELLNESS] = SubIndex("Community Wellness",  1500, 500, block.timestamp);
        subIndices[GOOD_SPEND]         = SubIndex("Good Spend",          1500, 500, block.timestamp);
    }

    // ─── Oracle Reporting ────────────────────────────────────────────

    function authorizeReporter(address reporter) external onlyOwner {
        oracleReporters[reporter] = true;
        emit ReporterAuthorized(reporter);
    }

    function revokeReporter(address reporter) external onlyOwner {
        oracleReporters[reporter] = false;
        emit ReporterRevoked(reporter);
    }

    function setLefCoinContract(address _lefCoin) external onlyOwner {
        lefCoinContract = _lefCoin;
    }

    /**
     * @notice Oracle reporters update off-chain subindices (0–4).
     */
    function updateSubIndex(uint256 id, uint256 score) external onlyReporter {
        require(id < GOOD_SPEND, "Use updateGoodSpend for index 5");
        require(score <= MAX_SCORE, "Score exceeds maximum");

        subIndices[id].score = score;
        subIndices[id].updatedAt = block.timestamp;

        emit SubIndexUpdated(id, score, msg.sender);
        emit LoveIndexUpdated(getLoveIndex());
    }

    /**
     * @notice Called by the LefCoin contract to update the Good Spend subindex.
     */
    function updateGoodSpend(uint256 score) external onlyLefCoin {
        require(score <= MAX_SCORE, "Score exceeds maximum");

        subIndices[GOOD_SPEND].score = score;
        subIndices[GOOD_SPEND].updatedAt = block.timestamp;

        emit SubIndexUpdated(GOOD_SPEND, score, msg.sender);
        emit LoveIndexUpdated(getLoveIndex());
    }

    // ─── Index Queries ───────────────────────────────────────────────

    /**
     * @notice Returns the composite LOVE Index (0–1000), a weighted average of all subindices.
     */
    function getLoveIndex() public view returns (uint256) {
        uint256 composite = 0;
        for (uint256 i = 0; i < NUM_SUBINDICES; i++) {
            composite += subIndices[i].score * subIndices[i].weight;
        }
        return composite / 10000; // weights sum to 10000
    }

    /**
     * @notice Returns score and metadata for a single subindex.
     */
    function getSubIndex(uint256 id) external view returns (
        string memory name,
        uint256 weight,
        uint256 score,
        uint256 updatedAt
    ) {
        require(id < NUM_SUBINDICES, "Invalid subindex");
        SubIndex storage s = subIndices[id];
        return (s.name, s.weight, s.score, s.updatedAt);
    }

    /**
     * @notice Returns all subindex scores as an array.
     */
    function getAllScores() external view returns (uint256[6] memory scores) {
        for (uint256 i = 0; i < NUM_SUBINDICES; i++) {
            scores[i] = subIndices[i].score;
        }
    }
}
