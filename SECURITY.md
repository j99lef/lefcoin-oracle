# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in the LefCoin oracle pipeline or smart contracts, please report it responsibly:

**Email:** lefcoin@campley.uk
**Subject:** `[SECURITY] Brief description`

Please do NOT open a public GitHub issue for security vulnerabilities.

We will acknowledge receipt within 48 hours and provide a timeline for a fix.

## Architecture Security

### Oracle Trust Model

The LefCoin LOVE Index uses a **centralized oracle reporter** model (standard for testnet). The oracle reporter wallet is authorized by the contract owner to submit subindex scores (0–1000).

**Current trust assumptions:**
- The oracle reporter is trusted to submit accurate sentiment data
- The contract owner can authorize/revoke reporters
- All oracle submissions are on-chain and publicly auditable via BaseScan
- The data pipeline source code is open for anyone to verify the methodology

**Future decentralization path:**
- Multi-reporter consensus (require N-of-M agreement)
- Chainlink oracle integration
- Community governance over reporter authorization

### Smart Contract Security

- All contracts use OpenZeppelin v5 audited base contracts
- `Ownable` access control for admin functions
- Score values are bounded (0–1000)
- Transfer fees are capped at 5% (`MAX_FEE_BPS = 500`)
- Contracts are deployed on Base Sepolia (testnet) — not handling real funds

### Pipeline Security

- All API keys are loaded from environment variables — never hardcoded
- The `.env` file is excluded from git via `.gitignore`
- The pipeline gracefully degrades if API keys are missing (Tier 1 sources need no auth)
- No user data is collected or stored
- All external API calls use HTTPS

## What We DON'T Claim

- This is a **testnet** project — no real monetary value is at stake
- Smart contracts have NOT been professionally audited
- The oracle is centralized — a single reporter currently submits all data
- Sentiment scores are algorithmic estimates, not ground truth

## Scope

The following are in scope for security reports:
- Smart contract vulnerabilities (reentrancy, overflow, access control)
- Oracle manipulation vectors
- Pipeline data injection or tampering risks
- Credential exposure in the repository
- Supply chain attacks (dependency vulnerabilities)

The following are out of scope:
- Base Sepolia testnet infrastructure
- Third-party API security (GDELT, WHO, etc.)
- Social engineering attacks
