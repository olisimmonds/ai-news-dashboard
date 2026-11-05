from ai_dashboard.categorisation import categorise, classify_kind


def test_energy_article_is_categorised_as_markets() -> None:
    result = categorise(
        "Electricity prices surge as Ofgem reviews grid charges",
        "",
        source_categories=("general",),
    )
    assert "markets-energy" in result.categories


def test_ai_article_is_categorised_as_ai() -> None:
    result = categorise(
        "OpenAI launches a new agentic model for enterprises",
        "The model can use tools and browse the web.",
        source_categories=("general",),
    )
    assert "ai-technology" in result.categories


def test_politics_article_is_general_but_not_keyword_major() -> None:
    result = categorise(
        "UK general election called as prime minister resigns",
        "The country will vote next month.",
    )
    assert result.categories == ("general",)
    assert result.kind == "routine"


def test_article_can_belong_to_multiple_categories() -> None:
    result = categorise(
        "AI data centres push up electricity prices and gas demand",
        "Investors are watching battery storage and grid connections.",
        source_categories=("general",),
    )
    assert "ai-technology" in result.categories
    assert "markets-energy" in result.categories
    assert "general" in result.categories


def test_source_category_is_always_applied() -> None:
    result = categorise("A quiet afternoon in the office", source_categories=("markets-energy",))
    assert result.categories == ("markets-energy",)


def test_unmatched_article_falls_back_to_general() -> None:
    result = categorise("A completely unrelated headline about cats")
    assert result.categories == ("general",)


def test_general_source_does_not_absorb_specialist_story() -> None:
    result = categorise(
        "NVIDIA unveils a new GPU for AI inference",
        source_categories=("general",),
    )
    assert "ai-technology" in result.categories
    assert "general" in result.categories


def test_kind_classification() -> None:
    assert classify_kind("Breaking: major bank collapses", "") == "routine"
    assert classify_kind("Researchers publish new benchmark results", "") == "research"
    assert classify_kind("Analysis: what the budget means for you", "") == "opinion"
    assert classify_kind("Company launches new product", "") == "company"
    assert classify_kind("A quiet update from the industry", "") == "routine"


def test_categories_are_returned_in_canonical_order() -> None:
    result = categorise(
        "AI and energy: data centres drive electricity demand",
        "The general election debate focused on technology.",
        source_categories=("general",),
    )
    assert result.categories == ("markets-energy", "ai-technology", "general")


def test_primary_category_uses_strongest_signal() -> None:
    result = categorise(
        "OpenAI and Nvidia announce new AI data centre",
        "The artificial intelligence model will use GPUs.",
        source_categories=("general",),
    )
    assert result.primary_category == "ai-technology"
