"""
LefCoin Sentiment Oracle Pipeline v2 — Multi-Source Edition
============================================================
Analyzes world sentiment across 5 off-chain subindices using 15+ data sources
and submits scores to the SentimentOracle contract on Base Sepolia.

Subindices:
  0 - Global Peace        (20%) — conflict events, peace treaties, diplomacy
  1 - Charitable Giving   (15%) — donation volumes, charitable activity
  2 - Social Sentiment    (20%) — public discourse tone, social media mood
  3 - Environmental Care  (15%) — emissions data, air quality, conservation
  4 - Community Wellness  (15%) — health metrics, education, civic participation

Subindex 5 (Good Spend, 15%) is handled on-chain by the LefCoin contract.

Data Sources (Tier 1 = no auth, Tier 2 = free key):
  TIER 1 — No Auth Required:
    - GDELT (global news coverage)
    - Open-Meteo (air quality, global)
    - UK Carbon Intensity (grid carbon, GB)
    - Hedonometer (daily happiness score)
    - Disease.sh (global health data)
    - World Bank API (16K+ indicators)
    - ReliefWeb (humanitarian reports)

  TIER 1b — Bees & Biodiversity:
    - GBIF (wild bee occurrence records)
    - iNaturalist (citizen-science bee observations)
    - FAOSTAT (managed beehive numbers)

  TIER 1c — Additional Environmental:
    - Global Forest Watch (deforestation monitoring)

  TIER 1d — Health & Development:
    - WHO GHO (Global Health Observatory indicators)
    - UN SDG API (Sustainable Development Goals)

  TIER 2 — Free API Key:
    - NewsAPI (headlines)
    - Twitter/X (social posts)
    - SerpAPI (Google search results)
    - Reddit (subreddit posts)
    - YouTube (video metadata)
    - AQICN/WAQI (air quality stations)

  TIER 3 — Local LLM (optional, for advanced sentiment):
    - Ollama / Tiiny Pocket Lab

All sources are aggregated per-subindex with configurable weights.
Pipeline runs on Campley GCP (phoenix-479815), NOT Groupe infra.

Usage:
  python sentiment_pipeline.py              # one-shot update
  python sentiment_pipeline.py --daemon     # run continuously (every 6 hours)
  python sentiment_pipeline.py --dry-run    # fetch + analyze, don't submit
"""

import os
import sys
import json
import time
import logging
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

# Load .env from project root (parent of oracle/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("lefbot-oracle")

# Lazy import requests (used everywhere)
try:
    import requests
except ImportError:
    log.error("requests not installed. Run: pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Config:
    # Blockchain
    rpc_url: str = ""
    oracle_address: str = ""
    reporter_private_key: str = ""

    # Tier 2 API keys (all optional — pipeline works without them)
    newsapi_key: str = ""
    twitter_bearer_token: str = ""
    serper_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    youtube_api_key: str = ""
    aqicn_token: str = ""

    # Pipeline settings
    update_interval_hours: int = 6
    use_local_llm: bool = False
    local_llm_endpoint: str = "http://localhost:8080/v1"

    @classmethod
    def from_env(cls):
        return cls(
            rpc_url=os.getenv("ORACLE_RPC_URL", "https://sepolia.base.org"),
            oracle_address=os.getenv("ORACLE_ADDRESS", ""),
            reporter_private_key=os.getenv("ORACLE_REPORTER_KEY", ""),
            newsapi_key=os.getenv("NEWSAPI_KEY", ""),
            twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
            serper_api_key=os.getenv("SERPER_API_KEY", ""),
            reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
            youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
            aqicn_token=os.getenv("AQICN_TOKEN", ""),
            use_local_llm=os.getenv("USE_LOCAL_LLM", "false").lower() == "true",
            local_llm_endpoint=os.getenv("LOCAL_LLM_ENDPOINT", "http://localhost:8080/v1"),
        )

    def available_sources(self) -> list[str]:
        """List which data sources are available based on configured keys."""
        sources = [
            # Tier 1: always available
            "gdelt", "open_meteo", "carbon_intensity", "hedonometer",
            "disease_sh", "world_bank", "reliefweb",
            # Tier 1b: bees & biodiversity
            "gbif_bees", "inaturalist_bees", "faostat_bees",
            # Tier 1c: environmental
            "global_forest_watch",
            # Tier 1d: health & development
            "who_gho", "un_sdg",
        ]
        if self.newsapi_key:
            sources.append("newsapi")
        if self.twitter_bearer_token:
            sources.append("twitter")
        if self.serper_api_key:
            sources.append("serper")
        if self.reddit_client_id and self.reddit_client_secret:
            sources.append("reddit")
        if self.youtube_api_key:
            sources.append("youtube")
        if self.aqicn_token:
            sources.append("aqicn")
        return sources


# ═══════════════════════════════════════════════════════════════════
# SENTIMENT ANALYSIS
# ═══════════════════════════════════════════════════════════════════

class SentimentAnalyzer:
    """Multi-mode sentiment analysis: Local LLM → keyword fallback."""

    POSITIVE = {
        "peace", "treaty", "agreement", "cooperation", "aid", "donation",
        "charity", "volunteer", "love", "hope", "growth", "recovery",
        "sustainable", "renewable", "conservation", "health", "education",
        "community", "celebrate", "progress", "breakthrough", "success",
        "kindness", "generosity", "innovation", "clean", "protect",
        "harmony", "prosperity", "freedom", "justice", "wellness",
        "solar", "wind", "recycle", "restore", "thrive", "flourish",
        "unity", "solidarity", "empower", "uplift", "heal", "cure",
        "vaccine", "immunize", "literacy", "graduate", "scholarship",
        "refuge", "rescue", "rebuild", "resilient", "champion",
    }

    NEGATIVE = {
        "war", "conflict", "attack", "violence", "crisis", "disaster",
        "pollution", "destruction", "poverty", "disease", "corruption",
        "fraud", "collapse", "threat", "bomb", "death", "famine",
        "drought", "extinction", "inequality", "injustice", "abuse",
        "terror", "missile", "siege", "casualty", "pandemic",
        "contamination", "deforestation", "toxic", "smog", "flood",
        "earthquake", "hurricane", "wildfire", "genocide", "massacre",
        "recession", "unemployment", "homelessness", "overdose",
        "trafficking", "exploit", "embargo", "sanction", "airstrike",
    }

    def __init__(self, config: Config):
        self.config = config

    def analyze(self, texts: list[str], context: str) -> float:
        """Analyze texts, return 0.0 (very negative) to 1.0 (very positive)."""
        if not texts:
            return 0.5  # neutral when no data
        if self.config.use_local_llm:
            score = self._analyze_llm(texts, context)
            if score is not None:
                return score
        return self._analyze_keyword(texts)

    def _analyze_llm(self, texts: list[str], context: str) -> Optional[float]:
        """Use local LLM for sentiment. Returns None on failure."""
        try:
            combined = "\n".join(f"- {t[:200]}" for t in texts[:50])
            prompt = (
                f"You are a sentiment analyst. Analyze the following {context} texts "
                f"and return ONLY a single number between 0 and 100 representing overall "
                f"sentiment (0=extremely negative, 50=neutral, 100=extremely positive).\n\n"
                f"Texts:\n{combined}\n\nScore:"
            )
            resp = requests.post(
                f"{self.config.local_llm_endpoint}/chat/completions",
                json={"model": "default", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 10, "temperature": 0.1},
                timeout=30,
            )
            result = resp.json()["choices"][0]["message"]["content"].strip()
            num = float("".join(c for c in result if c.isdigit() or c == "."))
            return max(0.0, min(1.0, num / 100.0))
        except Exception as e:
            log.warning(f"LLM analysis failed ({e}), falling back to keywords")
            return None

    def _analyze_keyword(self, texts: list[str]) -> float:
        """Keyword sentiment scoring."""
        pos = neg = 0
        for text in texts:
            for word in text.lower().split():
                w = word.strip(".,!?;:'\"()-[]")
                if w in self.POSITIVE:
                    pos += 1
                elif w in self.NEGATIVE:
                    neg += 1
        if pos + neg == 0:
            return 0.5
        return pos / (pos + neg)


# ═══════════════════════════════════════════════════════════════════
# DATA SOURCES — Each returns {"texts": [...], "scores": [...], "meta": {...}}
# texts = raw text for sentiment analysis
# scores = pre-computed numeric scores (0.0-1.0) from structured APIs
# ═══════════════════════════════════════════════════════════════════

class SourceResult:
    """Standardised result from a data source."""
    def __init__(self, name: str, texts: list[str] = None, scores: list[float] = None,
                 count: int = 0, error: str = None):
        self.name = name
        self.texts = texts or []
        self.scores = scores or []
        self.count = count or len(self.texts) + len(self.scores)
        self.error = error

    @property
    def has_data(self):
        return bool(self.texts or self.scores)


def _safe_fetch(name: str, fn, *args, **kwargs) -> SourceResult:
    """Wrapper that catches all exceptions from a data source."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.warning(f"  [{name}] failed: {e}")
        return SourceResult(name=name, error=str(e))


# ─── GDELT Rate Limiter ─────────────────────────────────────────
_gdelt_last_call = 0.0  # epoch timestamp of last GDELT API call
_GDELT_MIN_INTERVAL = 6.0  # seconds between GDELT calls (API requires 5s)

def _rate_limited_gdelt(query: str, max_results: int = 25) -> SourceResult:
    """GDELT fetch with rate limiting — enforces 6s gap between calls."""
    global _gdelt_last_call
    elapsed = time.time() - _gdelt_last_call
    if elapsed < _GDELT_MIN_INTERVAL:
        wait = _GDELT_MIN_INTERVAL - elapsed
        log.info(f"    [gdelt] Rate limit: waiting {wait:.1f}s...")
        time.sleep(wait)
    _gdelt_last_call = time.time()
    return fetch_gdelt(query, max_results)


# ─── TIER 1: No Auth Required ────────────────────────────────────

def fetch_gdelt(query: str, max_results: int = 25) -> SourceResult:
    """GDELT DOC 2.0 API — global news coverage, no auth needed.
    Note: GDELT requires OR-ed terms to be wrapped in parentheses."""
    # GDELT requires parentheses around OR-ed terms
    if " OR " in query and not query.startswith("("):
        query = f"({query})"
    resp = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "artlist",
            "maxrecords": max_results,
            "format": "json",
            "sort": "DateDesc",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return SourceResult(name="gdelt", error=f"HTTP {resp.status_code}: {resp.text[:100]}")
    try:
        data = resp.json()
    except Exception:
        return SourceResult(name="gdelt", error=f"Non-JSON response: {resp.text[:100]}")
    articles = data.get("articles", [])
    texts = [a.get("title", "") for a in articles if a.get("title")]
    return SourceResult(name="gdelt", texts=texts, count=len(texts))


def fetch_reliefweb(query: str, max_results: int = 20) -> SourceResult:
    """ReliefWeb API — UN OCHA humanitarian reports.
    Note: Requires approved appname since Nov 2025. Falls back gracefully."""
    resp = requests.post(
        "https://api.reliefweb.int/v1/reports",
        headers={"Content-Type": "application/json"},
        json={
            "appname": "lefcoin-oracle",
            "query": {"value": query},
            "limit": max_results,
            "fields": {"include": ["title"]},
            "sort": ["date:desc"],
        },
        timeout=15,
    )
    if resp.status_code == 403:
        return SourceResult(name="reliefweb", error="Appname not approved (apply at apidoc.reliefweb.int)")
    data = resp.json()
    reports = data.get("data", [])
    texts = [r["fields"]["title"] for r in reports if r.get("fields", {}).get("title")]
    return SourceResult(name="reliefweb", texts=texts, count=len(texts))


def fetch_hedonometer() -> SourceResult:
    """Hedonometer API — daily happiness score from social media, no auth.
    Dataset last updated May 2023 — we fetch the most recent available data
    as a baseline happiness indicator."""
    # Get total count first, then fetch the latest entries
    resp = requests.get(
        "https://hedonometer.org/api/v1/happiness/",
        params={"format": "json", "limit": 1},
        timeout=15,
    )
    data = resp.json()
    total = data.get("meta", {}).get("total_count", 0)
    if total == 0:
        return SourceResult(name="hedonometer", error="No data available")

    # Fetch last 30 entries (most recent data points)
    offset = max(0, total - 30)
    resp2 = requests.get(
        "https://hedonometer.org/api/v1/happiness/",
        params={"format": "json", "limit": 30, "offset": offset},
        timeout=15,
    )
    data2 = resp2.json()
    objects = data2.get("objects", [])
    if not objects:
        return SourceResult(name="hedonometer", error="No data at offset")

    # Hedonometer scores range ~5.0-6.5 on a 1-9 scale, normalize to 0-1
    # Filter: only keep positive happiness values (>1.0) as some entries are deltas
    scores = []
    for obj in objects:
        raw = float(obj.get("happiness", 0))
        if raw > 1.0:  # Skip delta/shift values (negative or near-zero)
            # Map 4.0-8.0 range to 0-1 (4=very sad, 6=neutral, 8=very happy)
            normalized = max(0.0, min(1.0, (raw - 4.0) / 4.0))
            scores.append(normalized)
    if not scores:
        return SourceResult(name="hedonometer", error="No valid happiness scores found")
    return SourceResult(name="hedonometer", scores=scores, count=len(scores))


def fetch_open_meteo_aqi(cities: list[tuple] = None) -> SourceResult:
    """Open-Meteo Air Quality API — hourly AQI, no auth needed.
    Returns normalized scores (lower pollution = higher score = better environment)."""
    if cities is None:
        # Major global cities: lat, lon, name
        cities = [
            (51.51, -0.13, "London"), (40.71, -74.01, "NYC"),
            (35.68, 139.69, "Tokyo"), (28.61, 77.23, "Delhi"),
            (39.90, 116.41, "Beijing"), (-23.55, -46.63, "Sao Paulo"),
            (48.86, 2.35, "Paris"), (55.75, 37.62, "Moscow"),
            (30.04, 31.24, "Cairo"), (-33.87, 151.21, "Sydney"),
        ]
    scores = []
    for lat, lon, name in cities:
        try:
            resp = requests.get(
                "https://air-quality-api.open-meteo.com/v1/air-quality",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "pm2_5,pm10,nitrogen_dioxide,ozone",
                },
                timeout=10,
            )
            data = resp.json()
            current = data.get("current", {})
            pm25 = current.get("pm2_5", 25)
            # WHO guideline: PM2.5 ≤ 15 µg/m³ is good. ≥ 75 is very unhealthy.
            # Invert: low pollution = high score
            score = max(0.0, min(1.0, 1.0 - (pm25 / 75.0)))
            scores.append(score)
        except Exception:
            continue
    return SourceResult(name="open_meteo_aqi", scores=scores, count=len(scores))


def fetch_uk_carbon_intensity() -> SourceResult:
    """UK National Grid Carbon Intensity API — no auth, 30-min granularity.
    Returns normalized score (lower carbon = higher score)."""
    resp = requests.get("https://api.carbonintensity.org.uk/intensity", timeout=10)
    data = resp.json()
    entries = data.get("data", [])
    if not entries:
        return SourceResult(name="uk_carbon", error="No data")
    intensity = entries[0].get("intensity", {})
    actual = intensity.get("actual") or intensity.get("forecast", 200)
    # UK grid: ~50 gCO2/kWh is very clean, ~400+ is dirty
    score = max(0.0, min(1.0, 1.0 - (actual / 400.0)))
    return SourceResult(name="uk_carbon", scores=[score], count=1)


def fetch_disease_sh() -> SourceResult:
    """Disease.sh — global health data (COVID as proxy for health crisis level), no auth.
    Returns normalized score (fewer new cases = better wellness)."""
    resp = requests.get("https://disease.sh/v3/covid-19/all", timeout=10)
    data = resp.json()
    # Use recovery rate as a health proxy
    cases = data.get("cases", 1)
    recovered = data.get("recovered", 0)
    active = data.get("active", 0)
    if cases > 0:
        recovery_rate = recovered / cases
        # Also factor in: are active cases declining?
        cases_per_million = data.get("casesPerOneMillion", 0)
        # Normalize: recovery rate of 0.95+ is good, below 0.5 is concerning
        score = max(0.0, min(1.0, recovery_rate))
    else:
        score = 0.5
    return SourceResult(name="disease_sh", scores=[score], count=1)


def fetch_world_bank(indicator: str, country: str = "WLD") -> SourceResult:
    """World Bank API — thousands of indicators, no auth.
    Returns most recent data point as raw value (caller normalizes)."""
    resp = requests.get(
        f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
        params={"format": "json", "per_page": 10, "date": "2018:2025"},
        timeout=15,
    )
    data = resp.json()
    if len(data) < 2 or not data[1]:
        return SourceResult(name=f"world_bank_{indicator}", error="No data")
    # Find most recent non-null value
    for entry in data[1]:
        val = entry.get("value")
        if val is not None:
            return SourceResult(
                name=f"world_bank_{indicator}",
                scores=[float(val)],
                count=1,
            )
    return SourceResult(name=f"world_bank_{indicator}", error="All null values")


# ─── TIER 1b: Bee / Pollinator / Biodiversity Sources ────────────

def fetch_gbif_bees(year: str = None) -> SourceResult:
    """GBIF Occurrence API — wild bee occurrence records globally, no auth.
    Measures biodiversity reporting activity as a proxy for pollinator health.
    More observations = more engaged monitoring = healthier ecosystems."""
    if year is None:
        year = str(datetime.now().year)
    resp = requests.get(
        "https://api.gbif.org/v1/occurrence/search",
        params={
            "taxonKey": 4334,  # Apoidea (all bees)
            "limit": 0,  # We just want the count
            "year": f"{int(year)-1},{year}",
        },
        timeout=15,
    )
    data = resp.json()
    count = data.get("count", 0)
    # Normalize: 500K+ observations in a year = very healthy monitoring
    # Below 100K = concerning decline in reporting
    score = max(0.0, min(1.0, count / 500000.0))
    return SourceResult(
        name="gbif_bees", scores=[score], count=1,
    )


def fetch_inaturalist_bees() -> SourceResult:
    """iNaturalist API — citizen-science bee observations, no auth.
    Tracks recent bee sighting activity as a proxy for pollinator presence."""
    d1 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    resp = requests.get(
        "https://api.inaturalist.org/v1/observations",
        params={
            "taxon_id": 630955,  # Apoidea (bees)
            "per_page": 0,  # Just get the count
            "d1": d1,
            "quality_grade": "research",
        },
        timeout=15,
    )
    data = resp.json()
    total = data.get("total_results", 0)
    # Normalize: 5000+ research-grade bee observations/month = excellent
    # Below 1000 = lower activity (seasonal or concerning)
    score = max(0.0, min(1.0, total / 5000.0))
    # Also fetch some texts for sentiment (species diversity indicator)
    resp2 = requests.get(
        "https://api.inaturalist.org/v1/observations/species_counts",
        params={"taxon_id": 630955, "d1": d1, "per_page": 5},
        timeout=15,
    )
    species_data = resp2.json()
    species_count = species_data.get("total_results", 0)
    texts = [f"Bee species diversity: {species_count} species observed globally in last 30 days"]
    if species_count > 200:
        texts.append("High bee species diversity indicates healthy pollinator ecosystems")
    elif species_count < 50:
        texts.append("Low bee species diversity may indicate pollinator decline")
    return SourceResult(name="inaturalist_bees", scores=[score], texts=texts, count=1)


def fetch_faostat_beehives() -> SourceResult:
    """FAOSTAT API — managed beehive numbers worldwide, no auth.
    Note: FAOSTAT API is frequently down. Falls back gracefully."""
    try:
        resp = requests.get(
            "https://fenixservices.fao.org/faostat/api/v1/en/data/QCL",
            params={
                "area": 5000,  # World aggregate
                "item": 1182,  # Honey, natural
                "element": 5510,  # Producing Animals (beehives)
                "year": "2020,2021,2022,2023",
                "output_type": "objects",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return SourceResult(name="faostat_bees", error=f"HTTP {resp.status_code}")
        data = resp.json()
        records = data.get("data", [])
        if not records:
            return SourceResult(name="faostat_bees", error="No records")
        # Get most recent value
        latest = max(records, key=lambda r: r.get("Year", 0))
        hives = float(latest.get("Value", 0))
        year = latest.get("Year", "?")
        # Global beehives: ~90-100M is healthy, below 80M is concerning
        score = max(0.0, min(1.0, hives / 100_000_000))
        texts = [f"Global managed beehives in {year}: {hives:,.0f}"]
        return SourceResult(name="faostat_bees", scores=[score], texts=texts, count=1)
    except Exception as e:
        return SourceResult(name="faostat_bees", error=f"FAOSTAT unavailable: {e}")


# ─── TIER 1c: Additional Environmental Sources ──────────────────

def fetch_global_forest_watch() -> SourceResult:
    """Global Forest Watch Data API — tree cover loss data, no auth.
    Measures deforestation trends as an environmental health indicator."""
    try:
        resp = requests.get(
            "https://data-api.globalforestwatch.org/dataset/umd_tree_cover_loss/latest",
            timeout=15,
        )
        if resp.status_code != 200:
            return SourceResult(name="gfw", error=f"HTTP {resp.status_code}")
        data = resp.json()
        metadata = data.get("data", {}).get("metadata", {})
        # GFW returns dataset metadata — we can extract trend info
        title = metadata.get("title", "Tree cover loss data")
        texts = [
            f"Global Forest Watch: {title}",
            "Deforestation monitoring active across tropical and temperate forests",
        ]
        # Use 0.5 as baseline — actual deforestation trend would need time series
        return SourceResult(name="gfw", texts=texts, scores=[0.5], count=1)
    except Exception as e:
        return SourceResult(name="gfw", error=str(e))


# ─── TIER 1d: Health / Development Sources ───────────────────────

def fetch_who_gho(indicator: str = "WHOSIS_000001") -> SourceResult:
    """WHO Global Health Observatory OData API — global health indicators, no auth.
    Default indicator: Life expectancy at birth (WHOSIS_000001)."""
    resp = requests.get(
        f"https://ghoapi.azureedge.net/api/{indicator}",
        params={
            "$filter": "SpatialDim eq 'GLOBAL' and Dim1 eq 'SEX_BTSX'",
            "$top": 5,
            "$orderby": "TimeDim desc",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return SourceResult(name=f"who_{indicator}", error=f"HTTP {resp.status_code}")
    data = resp.json()
    values = data.get("value", [])
    if not values:
        return SourceResult(name=f"who_{indicator}", error="No data")
    latest = values[0]
    raw = float(latest.get("NumericValue", 0))
    year = latest.get("TimeDim", "?")
    # Life expectancy: normalize 50-85 to 0-1
    score = max(0.0, min(1.0, (raw - 50) / 35.0))
    texts = [f"WHO Global life expectancy ({year}): {raw:.1f} years"]
    return SourceResult(name=f"who_{indicator}", scores=[score], texts=texts, count=1)


def fetch_un_sdg(indicator: str = "3.1.1", area: str = "1") -> SourceResult:
    """UN SDG API — Sustainable Development Goal indicators, no auth.
    Default: SDG 3.1.1 (Maternal mortality ratio per 100K live births).
    Area 1 = World."""
    resp = requests.get(
        "https://unstats.un.org/sdgs/UNSDGAPIV5/v1/sdg/Indicator/Data",
        params={
            "indicator": indicator,
            "areaCode": area,
            "pageSize": 5,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return SourceResult(name=f"un_sdg_{indicator}", error=f"HTTP {resp.status_code}")
    data = resp.json()
    records = data if isinstance(data, list) else data.get("data", [])
    if not records:
        return SourceResult(name=f"un_sdg_{indicator}", error="No data")
    # Get most recent record
    latest = records[-1] if isinstance(records, list) else records
    val = float(latest.get("value", 0))
    year = latest.get("timePeriodStart", "?")
    desc = latest.get("seriesDescription", indicator)
    # Maternal mortality: 0 is ideal, 500+ is very bad. Invert and normalize.
    if "mortality" in desc.lower() or "death" in desc.lower():
        score = max(0.0, min(1.0, 1.0 - (val / 500.0)))
    else:
        score = 0.5  # Unknown indicator type, use neutral
    texts = [f"UN SDG {indicator} ({year}): {val:.1f} — {desc}"]
    return SourceResult(name=f"un_sdg_{indicator}", scores=[score], texts=texts, count=1)


# ─── TIER 2: Free API Key Required ───────────────────────────────

def fetch_newsapi(config: Config, query: str, max_results: int = 50) -> SourceResult:
    """NewsAPI — headline sentiment, requires free key."""
    resp = requests.get(
        "https://newsapi.org/v2/everything",
        params={
            "q": query,
            "from": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            "sortBy": "relevancy",
            "pageSize": max_results,
            "apiKey": config.newsapi_key,
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("status") != "ok":
        return SourceResult(name="newsapi", error=data.get("message", "Unknown error"))
    articles = data.get("articles", [])
    texts = [f"{a.get('title', '')} {a.get('description', '')}"
             for a in articles if a.get("title")]
    return SourceResult(name="newsapi", texts=texts, count=len(texts))


def fetch_serper(config: Config, query: str, max_results: int = 10) -> SourceResult:
    """SerpAPI — Google search results, requires key.
    Supports both Serper.dev and SerpAPI.com key formats."""
    # Try SerpAPI.com first (longer keys), fall back to Serper.dev
    if len(config.serper_api_key) > 40:
        # SerpAPI.com format
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "q": query, "api_key": config.serper_api_key,
                "engine": "google", "num": max_results,
            },
            timeout=15,
        )
        data = resp.json()
        organic = data.get("organic_results", [])
        texts = [f"{r.get('title', '')} {r.get('snippet', '')}" for r in organic]
    else:
        # Serper.dev format
        resp = requests.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": config.serper_api_key},
            timeout=15,
        )
        data = resp.json()
        organic = data.get("organic", [])
        texts = [f"{r.get('title', '')} {r.get('snippet', '')}" for r in organic]
    return SourceResult(name="serper", texts=texts, count=len(texts))


def fetch_reddit(config: Config, query: str, max_results: int = 15) -> SourceResult:
    """Reddit — OAuth2 search, requires client_id + client_secret."""
    # Get access token
    auth_resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=(config.reddit_client_id, config.reddit_client_secret),
        headers={"User-Agent": "LefCoin-Oracle/2.0"},
        timeout=10,
    )
    token = auth_resp.json().get("access_token")
    if not token:
        return SourceResult(name="reddit", error="Auth failed")

    # Search
    resp = requests.get(
        "https://oauth.reddit.com/search",
        params={"q": query, "limit": max_results, "sort": "relevance", "t": "day"},
        headers={"Authorization": f"Bearer {token}", "User-Agent": "LefCoin-Oracle/2.0"},
        timeout=15,
    )
    data = resp.json()
    posts = data.get("data", {}).get("children", [])
    texts = [f"{p['data'].get('title', '')} {p['data'].get('selftext', '')[:200]}"
             for p in posts if p.get("data", {}).get("title")]
    return SourceResult(name="reddit", texts=texts, count=len(texts))


def fetch_youtube(config: Config, query: str, max_results: int = 10) -> SourceResult:
    """YouTube Data API v3 — video titles/descriptions, requires key."""
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "q": query, "key": config.youtube_api_key,
            "part": "snippet", "type": "video",
            "maxResults": max_results, "order": "relevance",
            "publishedAfter": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        timeout=15,
    )
    data = resp.json()
    items = data.get("items", [])
    texts = [f"{i['snippet'].get('title', '')} {i['snippet'].get('description', '')[:200]}"
             for i in items if i.get("snippet")]
    return SourceResult(name="youtube", texts=texts, count=len(texts))


def fetch_twitter(config: Config, query: str = "world sentiment -is:retweet lang:en",
                  max_results: int = 50) -> SourceResult:
    """Twitter/X API v2 — recent tweets, requires bearer token."""
    resp = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        params={"query": query, "max_results": min(max_results, 100), "tweet.fields": "text"},
        headers={"Authorization": f"Bearer {config.twitter_bearer_token}"},
        timeout=15,
    )
    data = resp.json()
    tweets = data.get("data", [])
    texts = [t["text"] for t in tweets if t.get("text")]
    return SourceResult(name="twitter", texts=texts, count=len(texts))


def fetch_aqicn(config: Config, city: str = "london") -> SourceResult:
    """AQICN/WAQI — real-time air quality index, requires free token."""
    resp = requests.get(
        f"https://api.waqi.info/feed/{city}/",
        params={"token": config.aqicn_token},
        timeout=10,
    )
    data = resp.json()
    if data.get("status") != "ok":
        return SourceResult(name="aqicn", error=data.get("message", "API error"))
    aqi = data.get("data", {}).get("aqi", 50)
    # AQI: 0-50 good, 51-100 moderate, 101-150 unhealthy sensitive, 151-200 unhealthy, 201+ very unhealthy
    score = max(0.0, min(1.0, 1.0 - (aqi / 200.0)))
    return SourceResult(name="aqicn", scores=[score], count=1)


# ═══════════════════════════════════════════════════════════════════
# SUBINDEX AGGREGATORS
# Each collects from multiple sources and produces a blended score
# ═══════════════════════════════════════════════════════════════════

class SubindexAggregator:
    """Aggregates multiple data sources into a single subindex score."""

    def __init__(self, config: Config, analyzer: SentimentAnalyzer):
        self.config = config
        self.analyzer = analyzer

    def _blend(self, results: list[SourceResult], context: str) -> tuple[int, dict]:
        """Blend multiple source results into a single 0-1000 score.
        Returns (score, metadata_dict)."""
        all_texts = []
        all_scores = []
        source_meta = {}

        for r in results:
            if r.error:
                source_meta[r.name] = {"status": "error", "error": r.error}
                continue
            if r.texts:
                all_texts.extend(r.texts)
                source_meta[r.name] = {"status": "ok", "texts": len(r.texts)}
            if r.scores:
                all_scores.extend(r.scores)
                source_meta[r.name] = source_meta.get(r.name, {})
                source_meta[r.name].update({"status": "ok", "scores": len(r.scores)})

        # Blend: weighted average of text sentiment and pre-computed scores
        text_score = self.analyzer.analyze(all_texts, context) if all_texts else None
        numeric_score = sum(all_scores) / len(all_scores) if all_scores else None

        if text_score is not None and numeric_score is not None:
            # 60% text sentiment, 40% numeric (structured data is more reliable)
            blended = text_score * 0.6 + numeric_score * 0.4
        elif text_score is not None:
            blended = text_score
        elif numeric_score is not None:
            blended = numeric_score
        else:
            blended = 0.5  # neutral fallback

        return int(blended * 1000), source_meta

    # ─── Per-Subindex Methods ────────────────────────────────────

    def score_peace(self) -> tuple[int, dict]:
        """Subindex 0: Global Peace — conflict/peace sentiment."""
        log.info("  [Peace] Fetching from multiple sources...")
        results = []

        # Tier 1 (always available)
        results.append(_safe_fetch("gdelt", _rate_limited_gdelt, "peace OR conflict OR war OR treaty"))
        results.append(_safe_fetch("reliefweb", fetch_reliefweb, "conflict peace"))

        # Tier 2 (if keys available)
        if self.config.newsapi_key:
            results.append(_safe_fetch("newsapi", fetch_newsapi, self.config,
                                       "peace OR conflict OR war OR treaty OR diplomacy"))
        if self.config.serper_api_key:
            results.append(_safe_fetch("serper", fetch_serper, self.config,
                                       "global peace index 2026 conflict resolution"))
        if self.config.reddit_client_id:
            results.append(_safe_fetch("reddit", fetch_reddit, self.config,
                                       "world peace geopolitics diplomacy"))
        if self.config.youtube_api_key:
            results.append(_safe_fetch("youtube", fetch_youtube, self.config,
                                       "world peace news today"))

        return self._blend(results, "global peace and conflict")

    def score_charity(self) -> tuple[int, dict]:
        """Subindex 1: Charitable Giving — donation/humanitarian activity."""
        log.info("  [Charity] Fetching from multiple sources...")
        results = []

        # Tier 1
        results.append(_safe_fetch("gdelt", _rate_limited_gdelt, "charity OR donation OR humanitarian"))
        results.append(_safe_fetch("reliefweb", fetch_reliefweb, "humanitarian aid donation"))

        # Tier 2
        if self.config.newsapi_key:
            results.append(_safe_fetch("newsapi", fetch_newsapi, self.config,
                                       "charity OR donation OR humanitarian OR volunteer OR giving"))
        if self.config.serper_api_key:
            results.append(_safe_fetch("serper", fetch_serper, self.config,
                                       "charitable giving donations 2026 philanthropy"))
        if self.config.reddit_client_id:
            results.append(_safe_fetch("reddit", fetch_reddit, self.config,
                                       "charity volunteer donation giving"))

        return self._blend(results, "charitable giving and donations")

    def score_social(self) -> tuple[int, dict]:
        """Subindex 2: Social Sentiment — public mood / discourse tone."""
        log.info("  [Social] Fetching from multiple sources...")
        results = []

        # Tier 1
        results.append(_safe_fetch("hedonometer", fetch_hedonometer))
        results.append(_safe_fetch("gdelt", _rate_limited_gdelt, "community society wellbeing people"))

        # Tier 2
        if self.config.twitter_bearer_token:
            results.append(_safe_fetch("twitter", fetch_twitter, self.config,
                                       "world today hope future -is:retweet lang:en"))
        if self.config.newsapi_key:
            results.append(_safe_fetch("newsapi", fetch_newsapi, self.config,
                                       "community OR society OR people OR hope OR future"))
        if self.config.reddit_client_id:
            results.append(_safe_fetch("reddit", fetch_reddit, self.config,
                                       "good news today positive"))
        if self.config.youtube_api_key:
            results.append(_safe_fetch("youtube", fetch_youtube, self.config,
                                       "positive news inspiring stories today"))

        return self._blend(results, "social sentiment and public mood")

    def score_environment(self) -> tuple[int, dict]:
        """Subindex 3: Environmental Care — air quality, carbon, conservation, biodiversity & bees."""
        log.info("  [Environment] Fetching from multiple sources...")
        results = []

        # Tier 1 (structured data — these are gold)
        results.append(_safe_fetch("open_meteo", fetch_open_meteo_aqi))
        results.append(_safe_fetch("uk_carbon", fetch_uk_carbon_intensity))
        results.append(_safe_fetch("gdelt", _rate_limited_gdelt,
                                   "environment OR climate OR conservation OR renewable"))

        # Tier 1b: Bee / Pollinator / Biodiversity
        results.append(_safe_fetch("gbif_bees", fetch_gbif_bees))
        results.append(_safe_fetch("inaturalist_bees", fetch_inaturalist_bees))
        results.append(_safe_fetch("faostat_bees", fetch_faostat_beehives))

        # Tier 1c: Forest / Deforestation
        results.append(_safe_fetch("gfw", fetch_global_forest_watch))

        # Tier 2
        if self.config.aqicn_token:
            for city in ["london", "new-york", "tokyo", "paris", "sydney"]:
                results.append(_safe_fetch(f"aqicn_{city}", fetch_aqicn, self.config, city))
        if self.config.newsapi_key:
            results.append(_safe_fetch("newsapi", fetch_newsapi, self.config,
                                       "environment OR climate OR conservation OR renewable OR pollution"))
        if self.config.serper_api_key:
            results.append(_safe_fetch("serper", fetch_serper, self.config,
                                       "air quality index today global environment news"))

        return self._blend(results, "environmental care, climate, biodiversity, and pollinator health")

    def score_wellness(self) -> tuple[int, dict]:
        """Subindex 4: Community Wellness — health, education, civic life."""
        log.info("  [Wellness] Fetching from multiple sources...")
        results = []

        # Tier 1 (structured data)
        results.append(_safe_fetch("disease_sh", fetch_disease_sh))

        # World Bank: life expectancy (SP.DYN.LE00.IN) — normalize 50-85 years to 0-1
        wb_result = _safe_fetch("world_bank", fetch_world_bank, "SP.DYN.LE00.IN")
        if wb_result.scores:
            raw_le = wb_result.scores[0]
            normalized = max(0.0, min(1.0, (raw_le - 50) / 35.0))
            wb_result.scores = [normalized]
        results.append(wb_result)

        # Tier 1d: WHO + UN SDG
        results.append(_safe_fetch("who_life_expectancy", fetch_who_gho, "WHOSIS_000001"))
        results.append(_safe_fetch("un_sdg_maternal", fetch_un_sdg, "3.1.1", "1"))

        # GDELT + news for text sentiment
        results.append(_safe_fetch("gdelt", _rate_limited_gdelt,
                                   "health OR education OR wellness OR community"))
        results.append(_safe_fetch("reliefweb", fetch_reliefweb, "health education"))

        # Tier 2
        if self.config.newsapi_key:
            results.append(_safe_fetch("newsapi", fetch_newsapi, self.config,
                                       "health OR education OR wellness OR community OR civic"))
        if self.config.reddit_client_id:
            results.append(_safe_fetch("reddit", fetch_reddit, self.config,
                                       "public health education community progress"))

        return self._blend(results, "community wellness and public health")


# ═══════════════════════════════════════════════════════════════════
# ON-CHAIN SUBMISSION
# ═══════════════════════════════════════════════════════════════════

class OracleSubmitter:
    """Submits sentiment scores to the SentimentOracle contract."""

    def __init__(self, config: Config):
        self.config = config

    def submit_scores(self, scores: dict[int, int], dry_run: bool = False):
        """Submit subindex scores to the oracle. scores: {id: score_0_to_1000}."""
        names = ["Global Peace", "Charitable Giving", "Social Sentiment",
                 "Environmental Care", "Community Wellness"]

        if dry_run:
            log.info("DRY RUN — scores that would be submitted:")
            for sid, score in sorted(scores.items()):
                if sid < 5:
                    log.info(f"  {names[sid]}: {score}/1000")
            return

        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
            account = w3.eth.account.from_key(self.config.reporter_private_key)

            oracle_abi = [{
                "inputs": [{"name": "id", "type": "uint256"}, {"name": "score", "type": "uint256"}],
                "name": "updateSubIndex", "outputs": [],
                "stateMutability": "nonpayable", "type": "function",
            }]

            oracle = w3.eth.contract(
                address=Web3.to_checksum_address(self.config.oracle_address),
                abi=oracle_abi,
            )

            nonce = w3.eth.get_transaction_count(account.address)

            for subindex_id in sorted(scores.keys()):
                if subindex_id >= 5:
                    continue
                score = max(0, min(1000, scores[subindex_id]))
                log.info(f"  Submitting {names[subindex_id]} = {score}")

                tx = oracle.functions.updateSubIndex(subindex_id, score).build_transaction({
                    "from": account.address,
                    "nonce": nonce,
                    "gas": 100000,
                    "gasPrice": w3.eth.gas_price,
                })
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                log.info(f"  ✓ {names[subindex_id]} updated (tx: {tx_hash.hex()[:16]}...)")
                nonce += 1

        except ImportError:
            log.error("web3 not installed. Run: pip install web3")
            self._log_scores(scores, names)
        except Exception as e:
            log.error(f"On-chain submission failed: {e}")
            traceback.print_exc()
            self._log_scores(scores, names)

    def _log_scores(self, scores, names):
        log.info("Scores (not submitted):")
        for sid, score in sorted(scores.items()):
            if sid < 5:
                log.info(f"  {names[sid]}: {score}/1000")


# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

class SentimentPipeline:
    """Multi-source oracle pipeline: fetch → analyze → blend → submit."""

    def __init__(self, config: Config):
        self.config = config
        self.analyzer = SentimentAnalyzer(config)
        self.aggregator = SubindexAggregator(config, self.analyzer)
        self.submitter = OracleSubmitter(config)

    def run_once(self, dry_run: bool = False) -> dict:
        """Run a single pipeline cycle. Returns full result dict."""
        log.info("=" * 70)
        log.info("LefCoin Oracle v2 — Multi-Source Sentiment Pipeline")
        log.info("=" * 70)

        available = self.config.available_sources()
        log.info(f"Available data sources ({len(available)}): {', '.join(available)}")
        log.info("")

        scores = {}
        all_meta = {}

        # 0: Global Peace
        log.info("─── Subindex 0: Global Peace (20%) ───")
        score, meta = self.aggregator.score_peace()
        scores[0] = score
        all_meta["peace"] = meta
        log.info(f"  → Score: {score}/1000 | Sources: {len([m for m in meta.values() if m.get('status') == 'ok'])}")

        # 1: Charitable Giving
        log.info("─── Subindex 1: Charitable Giving (15%) ───")
        score, meta = self.aggregator.score_charity()
        scores[1] = score
        all_meta["charity"] = meta
        log.info(f"  → Score: {score}/1000 | Sources: {len([m for m in meta.values() if m.get('status') == 'ok'])}")

        # 2: Social Sentiment
        log.info("─── Subindex 2: Social Sentiment (20%) ───")
        score, meta = self.aggregator.score_social()
        scores[2] = score
        all_meta["social"] = meta
        log.info(f"  → Score: {score}/1000 | Sources: {len([m for m in meta.values() if m.get('status') == 'ok'])}")

        # 3: Environmental Care
        log.info("─── Subindex 3: Environmental Care (15%) ───")
        score, meta = self.aggregator.score_environment()
        scores[3] = score
        all_meta["environment"] = meta
        log.info(f"  → Score: {score}/1000 | Sources: {len([m for m in meta.values() if m.get('status') == 'ok'])}")

        # 4: Community Wellness
        log.info("─── Subindex 4: Community Wellness (15%) ───")
        score, meta = self.aggregator.score_wellness()
        scores[4] = score
        all_meta["wellness"] = meta
        log.info(f"  → Score: {score}/1000 | Sources: {len([m for m in meta.values() if m.get('status') == 'ok'])}")

        # Composite (for logging — contract computes this)
        weights = [2000, 1500, 2000, 1500, 1500]
        composite = sum(scores[i] * weights[i] for i in range(5))
        off_chain_composite = composite // 8500
        log.info("")
        log.info(f"═══ Off-chain composite: {off_chain_composite}/1000 ═══")
        log.info("")

        # Submit
        log.info("Submitting to oracle..." if not dry_run else "DRY RUN — not submitting")
        self.submitter.submit_scores(scores, dry_run=dry_run)
        log.info("Cycle complete.\n")

        return {
            "timestamp": datetime.now().isoformat(),
            "composite": off_chain_composite,
            "scores": {str(k): v for k, v in scores.items()},
            "sources": {k: {sk: sv.get("status", "unknown") for sk, sv in v.items()}
                        for k, v in all_meta.items()},
            "available_sources": available,
        }

    def run_daemon(self, dry_run: bool = False):
        """Run continuously, updating every N hours."""
        log.info(f"Starting daemon mode (interval: {self.config.update_interval_hours}h)")
        while True:
            try:
                self.run_once(dry_run=dry_run)
            except Exception as e:
                log.error(f"Pipeline cycle failed: {e}")
                traceback.print_exc()
            sleep_sec = self.config.update_interval_hours * 3600
            log.info(f"Sleeping {self.config.update_interval_hours}h...")
            time.sleep(sleep_sec)


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    config = Config.from_env()

    if not config.oracle_address:
        log.warning("ORACLE_ADDRESS not set — running in dry-run mode")

    dry_run = "--dry-run" in sys.argv
    pipeline = SentimentPipeline(config)

    if "--daemon" in sys.argv:
        pipeline.run_daemon(dry_run=dry_run)
    else:
        result = pipeline.run_once(dry_run=dry_run)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
