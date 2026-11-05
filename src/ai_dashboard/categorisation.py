"""Transparent, rule-based categorisation and story-type classification.

The rules are intentionally simple and explainable. They are kept in one place so
they can be replaced or augmented by a better classifier later without touching
ingestion, storage or the API.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .config import CATEGORY_ORDER

# Weight 3 terms are strong enough to pull an article into a category on their own.
# Weight 1 terms add supporting evidence.
MARKETS_TERMS: dict[str, int] = {
    "interest rate": 3,
    "interest rates": 3,
    "central bank": 3,
    "bank of england": 3,
    "federal reserve": 3,
    "european central bank": 3,
    "inflation": 3,
    "deflation": 3,
    "recession": 3,
    "gdp": 3,
    "stock market": 3,
    "bond yield": 3,
    "bond yields": 3,
    "government bond": 3,
    "ftse 100": 3,
    "s&p 500": 3,
    "nasdaq": 3,
    "dow jones": 3,
    "oil price": 3,
    "oil prices": 3,
    "crude oil": 3,
    "brent crude": 3,
    "natural gas": 3,
    "gas prices": 3,
    "electricity price": 3,
    "electricity prices": 3,
    "electricity demand": 3,
    "power price": 3,
    "power prices": 3,
    "power demand": 3,
    "energy demand": 3,
    "energy market": 3,
    "energy markets": 3,
    "energy trading": 3,
    "battery storage": 3,
    "grid connection": 3,
    "power grid": 3,
    "national grid": 3,
    "offshore wind": 3,
    "wind farm": 3,
    "solar power": 3,
    "renewable energy": 3,
    "ofgem": 3,
    "elexon": 3,
    "neso": 3,
    "opec": 3,
    "lng": 3,
    "commodity prices": 3,
    "quantitative easing": 3,
    "interest-rate": 3,
    "monetary policy": 3,
    "fiscal policy": 3,
    "market": 1,
    "markets": 1,
    "shares": 1,
    "stocks": 1,
    "equities": 1,
    "investor": 1,
    "investors": 1,
    "earnings": 1,
    "revenue": 1,
    "profit": 1,
    "merger": 1,
    "acquisition": 1,
    "ipo": 1,
    "economy": 1,
    "economic": 1,
    "trade": 1,
    "tariff": 1,
    "tariffs": 1,
    "budget": 1,
    "fiscal": 1,
    "monetary": 1,
    "yield": 1,
    "yields": 1,
    "sterling": 1,
    "dollar": 1,
    "euro": 1,
    "energy": 1,
    "electricity": 1,
    "power": 1,
    "gas": 1,
    "oil": 1,
    "renewables": 1,
    "renewable": 1,
    "solar": 1,
    "wind": 1,
    "battery": 1,
    "storage": 1,
    "grid": 1,
    "utility": 1,
    "utilities": 1,
    "nuclear": 1,
    "hydrogen": 1,
    "coal": 1,
    "emissions": 1,
    "carbon": 1,
    "fuel": 1,
    "pipeline": 1,
    "commodities": 1,
    "fund": 1,
    "funds": 1,
    "bank": 1,
    "banking": 1,
    "lender": 1,
    "mortgage": 1,
    "mortgages": 1,
    "pound": 1,
    "currency": 1,
    "bitcoin": 1,
    "crypto": 1,
    "dividend": 1,
    "valuation": 1,
    "regulation": 1,
    "regulator": 1,
    "subsidy": 1,
    "subsidies": 1,
    "infrastructure": 1,
}

AI_TERMS: dict[str, int] = {
    "artificial intelligence": 3,
    "machine learning": 3,
    "deep learning": 3,
    "large language model": 3,
    "large language models": 3,
    "language model": 3,
    "foundation model": 3,
    "generative ai": 3,
    "neural network": 3,
    "neural networks": 3,
    "openai": 3,
    "anthropic": 3,
    "deepmind": 3,
    "hugging face": 3,
    "google gemini": 3,
    "chatgpt": 3,
    "claude": 3,
    "copilot": 3,
    "llm": 3,
    "llms": 3,
    "ai agent": 3,
    "ai agents": 3,
    "agentic": 3,
    "ai model": 3,
    "ai models": 3,
    "frontier model": 3,
    "foundation models": 3,
    "model release": 3,
    "semiconductor": 3,
    "semiconductors": 3,
    "data centre": 3,
    "data centres": 3,
    "data center": 3,
    "data centers": 3,
    "nvidia": 3,
    "tsmc": 3,
    "asml": 3,
    "gpu": 3,
    "gpus": 3,
    "tpu": 3,
    "inference": 3,
    "transformer model": 3,
    "alignment": 1,
    "ai": 3,
    "algorithms": 1,
    "algorithm": 1,
    "automation": 1,
    "robotics": 1,
    "robot": 1,
    "software": 1,
    "cloud computing": 1,
    "cloud": 1,
    "cybersecurity": 1,
    "cyber": 1,
    "technology": 1,
    "tech": 1,
    "startup": 1,
    "startups": 1,
    "platform": 1,
    "app": 1,
    "apps": 1,
    "quantum": 1,
    "silicon": 1,
    "processor": 1,
    "processors": 1,
    "chip": 1,
    "chips": 1,
    "compute": 1,
    "computing": 1,
    "supercomputer": 1,
    "open source": 1,
    "developer": 1,
    "developers": 1,
    "api": 1,
    "model": 1,
    "models": 1,
    "training": 1,
    "prompt": 1,
    "chatbot": 1,
    "chatbots": 1,
    "machine translation": 1,
    "computer vision": 1,
    "speech recognition": 1,
    "generative": 1,
    "agent": 1,
    "agents": 1,
    "microsoft": 1,
    "google": 1,
    "meta": 1,
    "amazon": 1,
    "apple": 1,
    "tesla": 1,
    "datacenter": 1,
    "datacentre": 1,
    "server": 1,
    "servers": 1,
    "enterprise": 1,
}

GENERAL_TERMS: dict[str, int] = {
    "prime minister": 3,
    "general election": 3,
    "election": 3,
    "referendum": 3,
    "parliament": 3,
    "white house": 3,
    "kremlin": 3,
    "nato": 3,
    "united nations": 3,
    "european union": 3,
    "supreme court": 3,
    "state visit": 3,
    "war": 3,
    "invasion": 3,
    "ceasefire": 3,
    "climate change": 3,
    "cost of living": 3,
    "nhs": 3,
    "storm": 3,
    "earthquake": 3,
    "wildfire": 3,
    "flood": 3,
    "government": 1,
    "minister": 1,
    "mp": 1,
    "politician": 1,
    "politics": 1,
    "political": 1,
    "president": 1,
    "congress": 1,
    "senate": 1,
    "downing street": 1,
    "westminster": 1,
    "brussels": 1,
    "diplomat": 1,
    "diplomatic": 1,
    "sanctions": 1,
    "military": 1,
    "army": 1,
    "troops": 1,
    "conflict": 1,
    "protest": 1,
    "court": 1,
    "judge": 1,
    "police": 1,
    "crime": 1,
    "immigration": 1,
    "asylum": 1,
    "terror": 1,
    "terrorism": 1,
    "attack": 1,
    "uk": 1,
    "britain": 1,
    "british": 1,
    "england": 1,
    "scotland": 1,
    "wales": 1,
    "northern ireland": 1,
    "london": 1,
    "europe": 1,
    "european": 1,
    "united states": 1,
    "america": 1,
    "china": 1,
    "russia": 1,
    "ukraine": 1,
    "middle east": 1,
    "israel": 1,
    "gaza": 1,
    "science": 1,
    "scientist": 1,
    "scientists": 1,
    "research": 1,
    "study": 1,
    "health": 1,
    "hospital": 1,
    "doctor": 1,
    "education": 1,
    "school": 1,
    "university": 1,
    "weather": 1,
    "climate": 1,
    "environment": 1,
    "transport": 1,
    "rail": 1,
    "strike": 1,
    "union": 1,
    "workers": 1,
    "housing": 1,
    "rent": 1,
    "benefits": 1,
    "poverty": 1,
}

KIND_PATTERNS: dict[str, tuple[str, ...]] = {
    "research": (
        "research",
        "study",
        "paper",
        "preprint",
        "arxiv",
        "benchmark",
        "findings",
        "researchers",
        "scientists",
        "dataset",
        "technical report",
        "evaluation",
        "experiment",
        "breakthrough",
        "model card",
        "peer-reviewed",
    ),
    "opinion": (
        "opinion",
        "analysis",
        "comment",
        "column",
        "editorial",
        "review",
        "perspective",
        "first person",
        "explainer",
        "what it means",
    ),
    "company": (
        "announces",
        "announced",
        "launches",
        "launched",
        "unveils",
        "acquires",
        "acquisition",
        "partnership",
        "invests",
        "investment",
        "funding",
        "raises",
        "ipo",
        "earnings",
        "results",
        "appoints",
        "ceo",
        "chief executive",
        "product",
        "release",
        "rollout",
        "deal",
        "merger",
        "contract",
    ),
}


def _build_patterns(terms: Iterable[str]) -> list[tuple[re.Pattern[str], int, str]]:
    patterns: list[tuple[re.Pattern[str], int, str]] = []
    for term in sorted(set(terms), key=len, reverse=True):
        escaped = re.escape(term)
        pattern = re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)
        patterns.append((pattern, 0, term))
    return patterns


def _build_term_map(terms: dict[str, int]) -> list[tuple[re.Pattern[str], int, str]]:
    return [
        (
            re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE),
            weight,
            term,
        )
        for term, weight in sorted(terms.items(), key=lambda item: len(item[0]), reverse=True)
    ]


_MARKETS_PATTERNS = _build_term_map(MARKETS_TERMS)
_AI_PATTERNS = _build_term_map(AI_TERMS)
_GENERAL_PATTERNS = _build_term_map(GENERAL_TERMS)
_KIND_PATTERNS = {kind: _build_patterns(terms) for kind, terms in KIND_PATTERNS.items()}


@dataclass(frozen=True, slots=True)
class CategorisationResult:
    categories: tuple[str, ...]
    kind: str
    scores: dict[str, int]

    @property
    def primary_category(self) -> str:
        if self.scores:
            return max(CATEGORY_ORDER, key=lambda category: self.scores.get(category, 0))
        return self.categories[0] if self.categories else "general"


def _scores(text: str, patterns: list[tuple[re.Pattern[str], int, str]]) -> int:
    total = 0
    for pattern, weight, _ in patterns:
        if pattern.search(text):
            total += weight
    return total


def classify_kind(title: str, summary: str) -> str:
    """Classify the story type using title first, then the summary."""
    title_lower = title.lower()
    summary_lower = summary.lower()
    for kind in ("research", "opinion", "company"):
        for pattern, _, _ in _KIND_PATTERNS[kind]:
            if pattern.search(title_lower):
                return kind
    for kind in ("research", "opinion", "company"):
        for pattern, _, _ in _KIND_PATTERNS[kind]:
            if pattern.search(summary_lower):
                return kind
    return "routine"


def categorise(
    title: str,
    summary: str = "",
    source_categories: Iterable[str] = (),
    threshold: int = 3,
) -> CategorisationResult:
    """Assign one or more categories to an article.

    Source categories act as a prior: an article from a markets feed is always
    included in Markets & Energy. Additional categories are added when the text
    contains enough evidence. Every article receives at least one category.
    """
    text = f"{title}\n{summary}".lower()
    defaults = {category for category in source_categories if category in CATEGORY_ORDER}

    raw_scores = {
        "markets-energy": _scores(text, _MARKETS_PATTERNS),
        "ai-technology": _scores(text, _AI_PATTERNS),
        "general": _scores(text, _GENERAL_PATTERNS),
    }
    scores = {
        category: score + (2 if category in defaults else 0)
        for category, score in raw_scores.items()
    }

    categories = set(defaults)
    for category, score in scores.items():
        if score >= threshold:
            categories.add(category)
    if not categories:
        categories.add("general")

    ordered = tuple(category for category in CATEGORY_ORDER if category in categories)
    return CategorisationResult(
        categories=ordered,
        kind=classify_kind(title, summary),
        scores=scores,
    )
