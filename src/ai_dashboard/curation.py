"""Curation tuning for story clustering, scoring and briefing selection.

Everything that controls ranking lives in this module so it can be adjusted in
one place after living with the dashboard. The values are deliberately
conservative: grouping unrelated stories is worse than leaving two related
stories apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CurationConfig:
    # Clustering ---------------------------------------------------------
    cluster_window_hours: float = 72.0
    max_story_span_hours: float = 36.0
    title_jaccard_threshold: float = 0.46
    title_containment_threshold: float = 0.75
    title_containment_min_shared: int = 3
    min_shared_title_tokens: int = 2
    min_title_tokens: int = 2

    # Scoring ------------------------------------------------------------
    coverage_weight: float = 0.42
    reliability_weight: float = 0.23
    relevance_weight: float = 0.20
    kind_weight: float = 0.15
    coverage_full_publishers: int = 4
    recency_half_life_hours: float = 18.0
    recency_floor: float = 0.55
    kind_signals: dict[str, float] = field(
        default_factory=lambda: {
            "research": 0.55,
            "opinion": 0.35,
            "company": 0.55,
            "routine": 0.50,
            "major": 0.60,
        }
    )

    # Selection ----------------------------------------------------------
    lead_max_age_hours: float = 48.0
    lead_min_score: float = 0.24
    major_threshold: float = 0.56
    major_max_age_hours: float = 48.0
    featured_count: int = 5
    featured_max_per_publisher: int = 1
    featured_max_per_category: int = 2
    section_count: int = 5
    section_max_per_publisher: int = 2
    latest_count: int = 8
    briefing_lookback_hours: float = 96.0

    @property
    def weights_total(self) -> float:
        return (
            self.coverage_weight
            + self.reliability_weight
            + self.relevance_weight
            + self.kind_weight
        )


CURATION = CurationConfig()
