// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./LefCoin.sol";
import "./GoodSpendRegistry.sol";

/**
 * @title LoveGovernance
 * @notice Community governance for the LefCoin ecosystem.
 *         LEF holders can propose and vote on new Good Spend destinations,
 *         making the registry community-driven rather than centrally controlled.
 *
 * How it works:
 *   1. Any LEF holder with >= proposalThreshold tokens can propose a destination.
 *   2. All LEF holders can vote FOR or AGAINST during the voting period.
 *   3. Votes are weighted by LEF balance at time of voting.
 *   4. If quorum is met and FOR > AGAINST, the destination is auto-registered.
 *   5. Proposals can also request removal of existing destinations.
 */
contract LoveGovernance is Ownable {

    LefCoin public lefCoin;
    GoodSpendRegistry public registry;

    // ─── Governance Parameters ───────────────────────────────────

    uint256 public proposalThreshold = 10_000 * 1e18;  // 10k LEF to propose
    uint256 public votingPeriod = 3 days;
    uint256 public quorumBps = 400;  // 4% of circulating supply must vote

    // ─── Proposal Tracking ───────────────────────────────────────

    uint256 public proposalCount;

    enum ProposalType { AddDestination, RemoveDestination }
    enum ProposalState { Active, Passed, Failed, Executed }

    struct Proposal {
        uint256 id;
        address proposer;
        ProposalType proposalType;

        // Destination details
        address destination;
        string  name;
        GoodSpendRegistry.Category category;

        // Voting
        uint256 forVotes;
        uint256 againstVotes;
        uint256 startTime;
        uint256 endTime;

        // State
        bool executed;
    }

    mapping(uint256 => Proposal) public proposals;
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    // ─── Events ──────────────────────────────────────────────────

    event ProposalCreated(
        uint256 indexed id,
        address proposer,
        ProposalType proposalType,
        address destination,
        string name
    );
    event Voted(uint256 indexed proposalId, address voter, bool support, uint256 weight);
    event ProposalExecuted(uint256 indexed id, bool passed);
    event GovernanceParameterUpdated(string param, uint256 newValue);

    // ─── Constructor ─────────────────────────────────────────────

    constructor(address _lefCoin, address _registry) Ownable(msg.sender) {
        lefCoin = LefCoin(_lefCoin);
        registry = GoodSpendRegistry(_registry);
    }

    // ─── Proposals ───────────────────────────────────────────────

    /**
     * @notice Propose adding a new Good Spend destination.
     */
    function proposeAddDestination(
        address destination,
        string calldata name,
        GoodSpendRegistry.Category category
    ) external returns (uint256) {
        require(
            lefCoin.balanceOf(msg.sender) >= proposalThreshold,
            "Below proposal threshold"
        );
        require(destination != address(0), "Zero address");

        proposalCount++;
        uint256 id = proposalCount;

        proposals[id] = Proposal({
            id: id,
            proposer: msg.sender,
            proposalType: ProposalType.AddDestination,
            destination: destination,
            name: name,
            category: category,
            forVotes: 0,
            againstVotes: 0,
            startTime: block.timestamp,
            endTime: block.timestamp + votingPeriod,
            executed: false
        });

        emit ProposalCreated(id, msg.sender, ProposalType.AddDestination, destination, name);
        return id;
    }

    /**
     * @notice Propose removing an existing Good Spend destination.
     */
    function proposeRemoveDestination(address destination) external returns (uint256) {
        require(
            lefCoin.balanceOf(msg.sender) >= proposalThreshold,
            "Below proposal threshold"
        );
        require(registry.isGoodDestination(destination), "Not a registered destination");

        proposalCount++;
        uint256 id = proposalCount;

        proposals[id] = Proposal({
            id: id,
            proposer: msg.sender,
            proposalType: ProposalType.RemoveDestination,
            destination: destination,
            name: "",
            category: GoodSpendRegistry.Category.Other,
            forVotes: 0,
            againstVotes: 0,
            startTime: block.timestamp,
            endTime: block.timestamp + votingPeriod,
            executed: false
        });

        emit ProposalCreated(id, msg.sender, ProposalType.RemoveDestination, destination, "");
        return id;
    }

    // ─── Voting ──────────────────────────────────────────────────

    /**
     * @notice Vote on a proposal. Weight = your LEF balance at time of vote.
     */
    function vote(uint256 proposalId, bool support) external {
        Proposal storage p = proposals[proposalId];
        require(p.id != 0, "Proposal does not exist");
        require(block.timestamp <= p.endTime, "Voting ended");
        require(!hasVoted[proposalId][msg.sender], "Already voted");

        uint256 weight = lefCoin.balanceOf(msg.sender);
        require(weight > 0, "No voting power");

        hasVoted[proposalId][msg.sender] = true;

        if (support) {
            p.forVotes += weight;
        } else {
            p.againstVotes += weight;
        }

        emit Voted(proposalId, msg.sender, support, weight);
    }

    // ─── Execution ───────────────────────────────────────────────

    /**
     * @notice Execute a proposal after voting ends. Anyone can call this.
     */
    function execute(uint256 proposalId) external {
        Proposal storage p = proposals[proposalId];
        require(p.id != 0, "Proposal does not exist");
        require(block.timestamp > p.endTime, "Voting not ended");
        require(!p.executed, "Already executed");

        p.executed = true;

        bool passed = _meetsQuorum(p) && p.forVotes > p.againstVotes;

        if (passed) {
            if (p.proposalType == ProposalType.AddDestination) {
                registry.registerDestination(p.destination, p.name, p.category);
            } else {
                registry.removeDestination(p.destination);
            }
        }

        emit ProposalExecuted(proposalId, passed);
    }

    // ─── Queries ─────────────────────────────────────────────────

    function getProposalState(uint256 proposalId) external view returns (ProposalState) {
        Proposal storage p = proposals[proposalId];
        require(p.id != 0, "Proposal does not exist");

        if (p.executed) {
            bool passed = _meetsQuorum(p) && p.forVotes > p.againstVotes;
            return passed ? ProposalState.Executed : ProposalState.Failed;
        }
        if (block.timestamp <= p.endTime) {
            return ProposalState.Active;
        }
        bool passed = _meetsQuorum(p) && p.forVotes > p.againstVotes;
        return passed ? ProposalState.Passed : ProposalState.Failed;
    }

    function _meetsQuorum(Proposal storage p) internal view returns (bool) {
        uint256 circulatingSupply = lefCoin.totalSupply() - lefCoin.balanceOf(address(lefCoin));
        uint256 quorum = (circulatingSupply * quorumBps) / 10000;
        return (p.forVotes + p.againstVotes) >= quorum;
    }

    // ─── Admin (adjustable governance parameters) ────────────────

    function setProposalThreshold(uint256 newThreshold) external onlyOwner {
        proposalThreshold = newThreshold;
        emit GovernanceParameterUpdated("proposalThreshold", newThreshold);
    }

    function setVotingPeriod(uint256 newPeriod) external onlyOwner {
        require(newPeriod >= 1 days, "Too short");
        votingPeriod = newPeriod;
        emit GovernanceParameterUpdated("votingPeriod", newPeriod);
    }

    function setQuorum(uint256 newQuorumBps) external onlyOwner {
        require(newQuorumBps <= 5000, "Quorum too high");
        quorumBps = newQuorumBps;
        emit GovernanceParameterUpdated("quorumBps", newQuorumBps);
    }
}
