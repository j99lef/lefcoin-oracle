# LefCoin (LEF) — Indexed by Love

A sentiment-indexed cryptocurrency on Base L2 whose holder rewards are amplified by global positivity.

**Live site:** [lefcoin.web.app](https://lefcoin.web.app)
**Methodology:** [lefcoin.web.app/methodology](https://lefcoin.web.app/methodology.html)

## The LOVE Index

The on-chain **LOVE Index** (0–1000) measures world sentiment across 6 weighted subindices:

| # | Subindex | Weight | Data |
|---|----------|--------|------|
| 0 | Global Peace | 20% | Oracle — GDELT, SerpAPI, Reddit, ReliefWeb |
| 1 | Charitable Giving | 15% | Oracle — GDELT, SerpAPI, Reddit, ReliefWeb |
| 2 | Social Sentiment | 20% | Oracle — Hedonometer, GDELT, Twitter/X, Reddit |
| 3 | Environmental Care | 15% | Oracle — Open-Meteo, UK Carbon, GBIF Bees, iNaturalist, FAOSTAT, GFW, AQICN, GDELT, SerpAPI |
| 4 | Community Wellness | 15% | Oracle — Disease.sh, World Bank, WHO GHO, UN SDG, GDELT, Reddit |
| 5 | Good Spend | 15% | **On-chain** — LEF spent at verified good destinations |

Subindices 0–4 are computed off-chain by the oracle pipeline (this repo) and submitted on-chain every 6 hours. Subindex 5 is computed entirely on-chain by the LefCoin contract itself.

## How It Works

1. **Hold LEF** → A 1% transfer fee feeds a rewards pool
2. **LOVE Index amplifies rewards** → At index 1000 (max positivity), rewards are 2x. At 0, rewards are 0x
3. **Good Spend** → Send LEF to verified charities/projects → boosts the on-chain Good Spend subindex → raises LOVE Index for everyone
4. **The loop:** doing good → higher index → bigger rewards → more incentive to do good

## Oracle Pipeline

The sentiment pipeline aggregates data from **18 open data sources**:

**Tier 1 (No API key needed):**
GDELT, Open-Meteo, UK Carbon Intensity, Hedonometer, Disease.sh, World Bank, ReliefWeb, GBIF, iNaturalist, FAOSTAT, Global Forest Watch, WHO GHO, UN SDG

**Tier 2 (Free API key):**
SerpAPI, Reddit, Twitter/X, AQICN, YouTube (coming soon)

### Run It Yourself

```bash
# 1. Clone
git clone https://github.com/j99lef/lefcoin-oracle.git
cd lefcoin-oracle

# 2. Configure (OPTIONAL — works without any API keys)
cp .env.example .env
# Edit .env to add API keys if desired

# 3. Install Python dependencies
pip install -r oracle/requirements.txt

# 4. Run the pipeline (dry run — shows scores without submitting)
python oracle/sentiment_pipeline.py

# 5. Run with on-chain submission (requires PRIVATE_KEY in .env)
python oracle/sentiment_pipeline.py --submit
```

## Smart Contracts

Deployed on **Base Sepolia** (testnet):

| Contract | Address |
|----------|---------|
| SentimentOracle | [`0x53F00D1530914C21725bD0A277Cd1443FED66FcD`](https://sepolia.basescan.org/address/0x53F00D1530914C21725bD0A277Cd1443FED66FcD) |
| GoodSpendRegistry | [`0x148eAc71306dfF6300E99f19b5d280Eefc9E37cC`](https://sepolia.basescan.org/address/0x148eAc71306dfF6300E99f19b5d280Eefc9E37cC) |
| LefCoin (LEF) | [`0x977c8452eEd662F9E6515Be1c5D328946520a005`](https://sepolia.basescan.org/address/0x977c8452eEd662F9E6515Be1c5D328946520a005) |
| LoveGovernance | [`0xE28400d3A57A8CB0243d629fd327bA4Ed7dc015c`](https://sepolia.basescan.org/address/0xE28400d3A57A8CB0243d629fd327bA4Ed7dc015c) |

### Build & Test

```bash
npm install
npx hardhat compile
npx hardhat test          # 21 tests
node simulation/run.js    # Full ecosystem simulation
```

### Deploy

```bash
npx hardhat run scripts/deploy.js --network baseSepolia
```

## Token Details

- **Name:** LefCoin
- **Symbol:** LEF
- **Supply:** 1,000,000,000 LEF
- **Transfer Fee:** 1% (capped at 5%)
- **Good Spend Bonus:** +1% additional for verified destinations
- **Standard:** ERC-20 (Solidity ^0.8.24, OpenZeppelin v5)

## Security

See [SECURITY.md](SECURITY.md) for our security policy, trust model, and how to report vulnerabilities.

**Key points:**
- All API keys are loaded from environment variables — never hardcoded
- Smart contracts use OpenZeppelin audited base contracts
- This is a testnet project — contracts have not been professionally audited
- The oracle is currently centralized (single reporter)

## Contributing

We welcome contributions to the LOVE Index! See [lefcoin.web.app/contribute](https://lefcoin.web.app/contribute.html) to propose new data sources, or open a PR.

## License

MIT
