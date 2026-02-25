"""Tests for community theme generation and ThemeMember schemas."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neighbor.models.aggregate_schemas import (
    CommunityTheme,
    ThemeMember,
    ThemeMemberCitation,
)
from neighbor.utils.aggregator import _build_theme_members, _generate_themes


# =============================================================================
# Sample data helpers
# =============================================================================

def _make_profile(
    name="John Doe",
    entity_category="Resident",
    entity_type="Individual",
    community_influence="Low",
    noted_stance="unknown",
    owns_adjacent_parcel="No",
    influence_justification="No known public role",
    classification="unknown",
    motivations=None,
    claims="A local resident with no public record.",
    citations=None,
):
    """Create a minimal profile dict matching NeighborProfile output."""
    return {
        "name": name,
        "entity_category": entity_category,
        "entity_type": entity_type,
        "community_influence": community_influence,
        "noted_stance": noted_stance,
        "owns_adjacent_parcel": owns_adjacent_parcel,
        "influence_justification": influence_justification,
        "entity_classification": classification,
        "approach_recommendations": {
            "motivations": motivations or [],
            "engage": "Standard outreach.",
        },
        "claims": claims,
        "citations": citations,
    }


SAMPLE_PROFILES = [
    _make_profile(
        name="Blue Star Dairy Farms",
        community_influence="High",
        owns_adjacent_parcel="Yes",
        influence_justification="Town supervisor; major farm owner",
        motivations=["farmland_preservation", "local_control"],
        claims="Blue Star Dairy Farms operates large dairy facilities...",
        citations=[
            {"title": "WPR Article", "url": "https://www.wpr.org/news/dairy", "date": "2024-05-14"},
            {"title": "Farm Report", "url": "https://www.midwestfarmreport.com/2024/05/14/blue-star", "date": "2024-05-14"},
        ],
    ),
    _make_profile(
        name="Cynthia M. LaValley",
        community_influence="Low",
        claims="A local landowner with no known public roles.",
        citations=None,
    ),
    _make_profile(
        name="Springfield Church",
        entity_category="Organization",
        entity_type="Religious",
        classification="religious",
        community_influence="Medium",
        claims="Springfield Church is a local place of worship.",
        citations=[
            {"title": "Church Website", "url": "https://springfieldchurch.org"},
        ],
    ),
    _make_profile(
        name="Town of Vienna",
        entity_category="Organization",
        entity_type="Municipal",
        classification="municipal",
        community_influence="High",
        noted_stance="neutral",
        claims="The Town of Vienna oversees local zoning.",
        citations=[
            {"title": "Town Site", "url": "https://viennawi.gov"},
            {"title": "Town Site", "url": "https://viennawi.gov"},  # duplicate URL
            {"title": "Minutes", "url": "https://viennawi.gov/minutes"},
            {"title": "Budget", "url": "https://viennawi.gov/budget"},
            {"title": "Extra", "url": "https://viennawi.gov/extra"},  # should be capped at 3
        ],
    ),
]


# =============================================================================
# ThemeMemberCitation schema tests
# =============================================================================


class TestThemeMemberCitation:
    def test_minimal_citation(self):
        c = ThemeMemberCitation(title="Source")
        assert c.title == "Source"
        assert c.url is None
        assert c.date is None

    def test_full_citation(self):
        c = ThemeMemberCitation(title="Article", url="https://example.com", date="2024-01-01")
        assert c.url == "https://example.com"
        assert c.date == "2024-01-01"

    def test_serialization_roundtrip(self):
        c = ThemeMemberCitation(title="Test", url="https://x.com")
        d = c.dict()
        assert d["title"] == "Test"
        c2 = ThemeMemberCitation(**d)
        assert c2.title == c.title


# =============================================================================
# ThemeMember schema tests
# =============================================================================


class TestThemeMember:
    def test_minimal_member(self):
        m = ThemeMember(name="John Doe", persona="Local resident")
        assert m.name == "John Doe"
        assert m.persona == "Local resident"
        assert m.influence == "Low"
        assert m.adjacent is False
        assert m.citations == []

    def test_full_member(self):
        m = ThemeMember(
            name="Jane Smith",
            persona="Township board member; active in zoning",
            influence="High",
            adjacent=True,
            citations=[
                ThemeMemberCitation(title="Article", url="https://example.com"),
            ],
        )
        assert m.adjacent is True
        assert m.influence == "High"
        assert len(m.citations) == 1

    def test_serialization_roundtrip(self):
        m = ThemeMember(
            name="Test Person",
            persona="Farmer",
            influence="Medium",
            adjacent=True,
            citations=[ThemeMemberCitation(title="Src", url="https://a.com")],
        )
        d = m.dict()
        assert d["adjacent"] is True
        assert d["citations"][0]["url"] == "https://a.com"
        m2 = ThemeMember(**d)
        assert m2.name == m.name
        assert m2.citations[0].url == m.citations[0].url


# =============================================================================
# CommunityTheme with members tests
# =============================================================================


class TestCommunityThemeMembers:
    def test_backward_compat_no_members(self):
        """Old theme data without members field still works."""
        t = CommunityTheme(
            theme="Agricultural Community",
            description="Farmers in the area.",
            neighbor_count=5,
        )
        assert t.members == []

    def test_theme_with_members(self):
        t = CommunityTheme(
            theme="Test Theme",
            description="Description.",
            neighbor_count=2,
            members=[
                ThemeMember(name="A", persona="Farmer"),
                ThemeMember(name="B", persona="Rancher"),
            ],
        )
        assert len(t.members) == 2
        assert t.members[0].name == "A"

    def test_dict_roundtrip_preserves_members(self):
        """Verify members survive .dict() → CommunityTheme(**d) roundtrip."""
        t = CommunityTheme(
            theme="Theme",
            description="Desc.",
            neighbor_count=1,
            members=[
                ThemeMember(
                    name="Person",
                    persona="Bio line",
                    citations=[ThemeMemberCitation(title="T", url="https://x.com")],
                ),
            ],
        )
        d = t.dict()
        assert "members" in d
        assert len(d["members"]) == 1
        assert d["members"][0]["citations"][0]["url"] == "https://x.com"

        t2 = CommunityTheme(**d)
        assert t2.members[0].name == "Person"

    def test_json_roundtrip_preserves_members(self):
        """Verify members survive JSON serialization (the actual pipeline path)."""
        t = CommunityTheme(
            theme="Theme",
            description="Desc.",
            neighbor_count=1,
            members=[
                ThemeMember(name="P", persona="Bio", adjacent=True),
            ],
        )
        json_str = t.json()
        d = json.loads(json_str)
        assert d["members"][0]["adjacent"] is True

        t2 = CommunityTheme(**d)
        assert t2.members[0].adjacent is True

    def test_old_json_without_members_key(self):
        """Simulate loading old JSON that lacks 'members' key entirely."""
        old_data = {
            "theme": "Legacy Theme",
            "description": "Old format.",
            "neighbor_count": 3,
            "prevalent_concerns": ["farmland"],
            "typical_influence": "Low",
            "engagement_approach": "Community meetings.",
        }
        t = CommunityTheme(**old_data)
        assert t.members == []


# =============================================================================
# _build_theme_members tests
# =============================================================================


class TestBuildThemeMembers:
    def test_basic_assignment(self):
        assignments = [
            {"neighbor_index": 1, "persona": "Legacy dairy farmer; 4,000-acre family operation"},
        ]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members) == 1
        m = members[0]
        assert m.name == "Blue Star Dairy Farms"
        assert m.persona == "Legacy dairy farmer; 4,000-acre family operation"
        assert m.influence == "High"
        assert m.adjacent is True

    def test_citations_extracted(self):
        """Citations from profile are extracted and deduplicated."""
        assignments = [{"neighbor_index": 1, "persona": "Farmer"}]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members[0].citations) == 2
        assert members[0].citations[0].title == "WPR Article"

    def test_null_citations_handled(self):
        """Profile with citations=None produces empty citations list."""
        assignments = [{"neighbor_index": 2, "persona": "Landowner"}]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members) == 1
        assert members[0].citations == []

    def test_citations_capped_at_3(self):
        """Even if profile has 5 unique citations, only 3 are kept."""
        assignments = [{"neighbor_index": 4, "persona": "Municipal body"}]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members) == 1
        assert len(members[0].citations) == 3

    def test_citations_deduplicated_by_url(self):
        """Duplicate URLs in citations are skipped."""
        assignments = [{"neighbor_index": 4, "persona": "Municipal body"}]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        urls = [c.url for c in members[0].citations]
        assert len(urls) == len(set(urls))

    def test_adjacent_flag_mapping(self):
        """owns_adjacent_parcel='Yes' maps to adjacent=True."""
        assignments = [
            {"neighbor_index": 1, "persona": "Adjacent farmer"},
            {"neighbor_index": 2, "persona": "Non-adjacent resident"},
        ]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert members[0].adjacent is True
        assert members[1].adjacent is False

    def test_persona_truncated_at_100_chars(self):
        long_persona = "A" * 200
        assignments = [{"neighbor_index": 1, "persona": long_persona}]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members[0].persona) == 100

    def test_out_of_range_index_skipped(self):
        assignments = [
            {"neighbor_index": 0, "persona": "Zero index"},    # 0 → -1, invalid
            {"neighbor_index": 99, "persona": "Too high"},     # beyond list
            {"neighbor_index": 1, "persona": "Valid"},          # OK
        ]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        assert len(members) == 1
        assert members[0].name == "Blue Star Dairy Farms"

    def test_malformed_assignment_skipped(self):
        assignments = [
            {"neighbor_index": "not_a_number", "persona": "Bad"},
            {"persona": "Missing index"},
            {"neighbor_index": 1, "persona": "Valid"},
        ]
        members = _build_theme_members(assignments, SAMPLE_PROFILES)
        # "not_a_number" raises ValueError → skipped
        # Missing index defaults to 0 → becomes -1 → skipped
        assert len(members) == 1

    def test_empty_assignments(self):
        members = _build_theme_members([], SAMPLE_PROFILES)
        assert members == []

    def test_non_dict_citations_skipped(self):
        """If citations contain non-dict items, they are skipped."""
        profiles = [
            _make_profile(
                name="Weird Citations",
                citations=["not_a_dict", 42, {"title": "Valid", "url": "https://v.com"}],
            ),
        ]
        assignments = [{"neighbor_index": 1, "persona": "Test"}]
        members = _build_theme_members(assignments, profiles)
        assert len(members[0].citations) == 1
        assert members[0].citations[0].title == "Valid"

    def test_influence_capitalized(self):
        """community_influence='high' becomes 'High'."""
        profiles = [_make_profile(name="Test", community_influence="high")]
        assignments = [{"neighbor_index": 1, "persona": "X"}]
        members = _build_theme_members(assignments, profiles)
        assert members[0].influence == "High"

    def test_influence_default_when_none(self):
        """None community_influence defaults to 'Low'."""
        profiles = [_make_profile(name="Test", community_influence=None)]
        # Need to explicitly set None (default in helper is "Low")
        profiles[0]["community_influence"] = None
        assignments = [{"neighbor_index": 1, "persona": "X"}]
        members = _build_theme_members(assignments, profiles)
        assert members[0].influence == "Low"


# =============================================================================
# _generate_themes integration tests (mocked LLM)
# =============================================================================


class TestGenerateThemes:
    """Tests for _generate_themes with mocked Gemini API."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            # Remove any GEMINI_API_KEY / GOOGLE_API_KEY
            result = await _generate_themes(SAMPLE_PROFILES, "Test location")
            assert result == []

    @pytest.mark.asyncio
    async def test_successful_theme_generation(self):
        """Mock Gemini to return valid themes with member_assignments."""
        mock_response_data = [
            {
                "theme": "Agricultural Community",
                "description": "Farming families in the area.",
                "neighbor_count": 2,
                "prevalent_concerns": ["farmland_preservation"],
                "typical_influence": "Medium",
                "engagement_approach": "Community meetings.",
                "member_assignments": [
                    {"neighbor_index": 1, "persona": "Large dairy operation"},
                    {"neighbor_index": 2, "persona": "Local landowner"},
                ],
            },
            {
                "theme": "Institutional Presence",
                "description": "Churches and municipal bodies.",
                "neighbor_count": 2,
                "prevalent_concerns": ["community_impact"],
                "typical_influence": "Medium",
                "engagement_approach": "Formal engagement.",
                "member_assignments": [
                    {"neighbor_index": 3, "persona": "Local church"},
                    {"neighbor_index": 4, "persona": "Town government"},
                ],
            },
            {
                "theme": "Residential Cluster",
                "description": "No residents in this group.",
                "neighbor_count": 0,
                "prevalent_concerns": [],
                "typical_influence": "Low",
                "engagement_approach": "N/A",
                "member_assignments": [],
            },
            {
                "theme": "Active Community Members",
                "description": "Publicly engaged individuals.",
                "neighbor_count": 0,
                "prevalent_concerns": [],
                "typical_influence": "High",
                "engagement_approach": "Direct formal engagement.",
                "member_assignments": [],
            },
        ]

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_response_data)

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            with patch("neighbor.utils.aggregator.genai.Client", return_value=mock_client):
                themes = await _generate_themes(SAMPLE_PROFILES, "Test location")

        assert len(themes) == 4
        # First theme should have members populated from profiles
        assert themes[0].theme == "Agricultural Community"
        assert len(themes[0].members) == 2
        assert themes[0].members[0].name == "Blue Star Dairy Farms"
        assert themes[0].members[0].adjacent is True
        assert themes[0].members[1].name == "Cynthia M. LaValley"
        # Empty theme
        assert themes[2].members == []
        # Active Community Members
        assert themes[3].theme == "Active Community Members"

    @pytest.mark.asyncio
    async def test_empty_gemini_response(self):
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            with patch("neighbor.utils.aggregator.genai.Client", return_value=mock_client):
                themes = await _generate_themes(SAMPLE_PROFILES, "Test location")
        assert themes == []

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self):
        mock_response = MagicMock()
        mock_response.text = "not valid json {{{{"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            with patch("neighbor.utils.aggregator.genai.Client", return_value=mock_client):
                themes = await _generate_themes(SAMPLE_PROFILES, "Test location")
        assert themes == []

    @pytest.mark.asyncio
    async def test_malformed_member_assignments_graceful(self):
        """If member_assignments is not a list, fall back to empty members."""
        mock_response_data = [
            {
                "theme": "Test",
                "description": "Desc.",
                "neighbor_count": 1,
                "member_assignments": "not_a_list",
            },
        ]
        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_response_data)

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            with patch("neighbor.utils.aggregator.genai.Client", return_value=mock_client):
                themes = await _generate_themes(SAMPLE_PROFILES, "Test location")
        assert len(themes) == 1
        assert themes[0].members == []

    @pytest.mark.asyncio
    async def test_prompt_includes_names_and_claims(self):
        """Verify the prompt sent to Gemini includes neighbor names and claims snippets."""
        mock_response = MagicMock()
        mock_response.text = "[]"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            with patch("neighbor.utils.aggregator.genai.Client", return_value=mock_client) as mock_cls:
                await _generate_themes(SAMPLE_PROFILES, "Test location")

        # Inspect the prompt that was sent
        call_args = mock_client.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[0]
        if not isinstance(prompt, str):
            prompt = str(prompt)
        assert 'name="Blue Star Dairy Farms"' in prompt
        assert "claims_snippet=" in prompt
        assert "Active Community Members" in prompt
        assert "exactly 4" in prompt
