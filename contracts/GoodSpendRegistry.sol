// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title GoodSpendRegistry
 * @notice A curated registry of addresses where spending LefCoin counts as a
 *         "good spend" — charities, sustainable businesses, community projects, etc.
 *
 *         When LefCoin is transferred to a registered good-spend destination,
 *         the transaction boosts the on-chain Good Spend subindex in the
 *         SentimentOracle, increasing the overall LOVE Index and rewarding all holders.
 */
contract GoodSpendRegistry is Ownable {

    enum Category {
        Charity,
        Sustainability,
        Education,
        Healthcare,
        CommunityProject,
        DisasterRelief,
        AnimalWelfare,
        ArtsAndCulture,
        Other
    }

    struct GoodDestination {
        bool    registered;
        string  name;
        Category category;
        uint256 totalReceived;  // cumulative LEF received
        uint256 registeredAt;
    }

    mapping(address => GoodDestination) public destinations;
    address[] public destinationList;

    // Cumulative stats
    uint256 public totalGoodSpendVolume;
    uint256 public totalGoodTransactions;

    // Only LefCoin contract can record good spends
    address public lefCoinContract;

    event DestinationRegistered(address indexed dest, string name, Category category);
    event DestinationRemoved(address indexed dest);
    event GoodSpendRecorded(address indexed dest, uint256 amount);

    modifier onlyLefCoin() {
        require(msg.sender == lefCoinContract, "Only LefCoin contract");
        _;
    }

    constructor() Ownable(msg.sender) {}

    function setLefCoinContract(address _lefCoin) external onlyOwner {
        lefCoinContract = _lefCoin;
    }

    // ─── Registry Management ─────────────────────────────────────────

    function registerDestination(
        address dest,
        string calldata name,
        Category category
    ) external onlyOwner {
        require(!destinations[dest].registered, "Already registered");
        require(dest != address(0), "Zero address");

        destinations[dest] = GoodDestination({
            registered: true,
            name: name,
            category: category,
            totalReceived: 0,
            registeredAt: block.timestamp
        });
        destinationList.push(dest);

        emit DestinationRegistered(dest, name, category);
    }

    function removeDestination(address dest) external onlyOwner {
        require(destinations[dest].registered, "Not registered");
        destinations[dest].registered = false;
        emit DestinationRemoved(dest);
    }

    // ─── Queries ─────────────────────────────────────────────────────

    function isGoodDestination(address dest) external view returns (bool) {
        return destinations[dest].registered;
    }

    function getDestinationCount() external view returns (uint256) {
        return destinationList.length;
    }

    // ─── Recording (called by LefCoin contract) ──────────────────────

    function recordGoodSpend(address dest, uint256 amount) external onlyLefCoin {
        require(destinations[dest].registered, "Not a good destination");
        destinations[dest].totalReceived += amount;
        totalGoodSpendVolume += amount;
        totalGoodTransactions += 1;
        emit GoodSpendRecorded(dest, amount);
    }
}
