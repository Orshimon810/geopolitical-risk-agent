"""
Tests for src/georisk_agent/agents/archetypes.py

Covers:
  - Registry completeness (all 15 archetypes present)
  - Data integrity (field types, non-empty values, valid verdicts)
  - Regex validity (forbidden_prose_patterns compile without error)
  - Lookup helpers (get_archetype, get_ticker_archetype)
  - Archetype distinctions (regression-prevention: ASML ≠ foundry, TSM ≠ equipment)
"""

import re
import pytest
from georisk_agent.agents.archetypes import (
    ARCHETYPE_REGISTRY,
    TICKER_ARCHETYPE_MAP,
    ArchetypeRules,
    get_archetype,
    get_ticker_archetype,
)


_EXPECTED_ARCHETYPE_IDS = {
    "fabless_ai_chip_designer",
    "semiconductor_foundry",
    "semiconductor_equipment_supplier",
    "consumer_electronics",
    "defense_contractor",
    "software_platform",
    "bank",
    "airline",
    "retailer",
    "healthcare_insurer",
    "commodity_producer",
    "commodity_consumer",
    "automaker",
    "ev_manufacturer",
    "battery_or_lithium_supplier",
}

_VALID_VERDICTS = {"Bullish", "Bearish", "Neutral"}


# ---------------------------------------------------------------------------
# TestArchetypeRegistry
# ---------------------------------------------------------------------------

class TestArchetypeRegistry:
    def test_all_15_archetypes_present(self):
        assert len(ARCHETYPE_REGISTRY) == 15
        for expected_id in _EXPECTED_ARCHETYPE_IDS:
            assert expected_id in ARCHETYPE_REGISTRY, (
                f"Missing archetype in registry: '{expected_id}'"
            )

    def test_archetype_id_matches_registry_key(self):
        for key, rules in ARCHETYPE_REGISTRY.items():
            assert rules.archetype_id == key, (
                f"archetype_id '{rules.archetype_id}' does not match registry key '{key}'"
            )

    def test_all_display_names_non_empty(self):
        for rules in ARCHETYPE_REGISTRY.values():
            assert rules.display_name, (
                f"Empty display_name for archetype '{rules.archetype_id}'"
            )

    def test_all_fields_are_correct_types(self):
        for rules in ARCHETYPE_REGISTRY.values():
            assert isinstance(rules.archetype_id, str)
            assert isinstance(rules.display_name, str)
            assert isinstance(rules.typical_exposure_channels, tuple)
            assert isinstance(rules.forbidden_prose_patterns, tuple)
            assert isinstance(rules.event_sensitivities, dict)
            assert isinstance(rules.timing_constraints, tuple)
            assert isinstance(rules.verdict_calibration, tuple)

    def test_all_archetypes_have_at_least_one_exposure_channel(self):
        for rules in ARCHETYPE_REGISTRY.values():
            assert len(rules.typical_exposure_channels) >= 1, (
                f"Archetype '{rules.archetype_id}' has no exposure channels"
            )

    def test_all_archetypes_have_at_least_one_timing_constraint(self):
        for rules in ARCHETYPE_REGISTRY.values():
            assert len(rules.timing_constraints) >= 1, (
                f"Archetype '{rules.archetype_id}' has no timing constraints"
            )

    def test_all_archetypes_have_at_least_one_verdict_calibration_rule(self):
        for rules in ARCHETYPE_REGISTRY.values():
            assert len(rules.verdict_calibration) >= 1, (
                f"Archetype '{rules.archetype_id}' has no verdict calibration rules"
            )

    def test_forbidden_patterns_compile_as_regex(self):
        for rules in ARCHETYPE_REGISTRY.values():
            for pattern in rules.forbidden_prose_patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    pytest.fail(
                        f"Archetype '{rules.archetype_id}' has invalid regex "
                        f"pattern '{pattern}': {exc}"
                    )

    def test_event_sensitivities_values_are_valid_verdicts(self):
        for rules in ARCHETYPE_REGISTRY.values():
            for event_type, verdict in rules.event_sensitivities.items():
                assert verdict in _VALID_VERDICTS, (
                    f"Archetype '{rules.archetype_id}' event_sensitivity "
                    f"'{event_type}' has invalid verdict '{verdict}'"
                )

    def test_all_event_sensitivity_keys_are_strings(self):
        for rules in ARCHETYPE_REGISTRY.values():
            for k, v in rules.event_sensitivities.items():
                assert isinstance(k, str)
                assert isinstance(v, str)


# ---------------------------------------------------------------------------
# TestArchetypeLookup
# ---------------------------------------------------------------------------

class TestArchetypeLookup:
    def test_get_archetype_known_id_returns_correct_instance(self):
        rules = get_archetype("fabless_ai_chip_designer")
        assert rules is not None
        assert isinstance(rules, ArchetypeRules)
        assert rules.archetype_id == "fabless_ai_chip_designer"
        assert rules.display_name == "Fabless AI Chip Designer"

    def test_get_archetype_returns_same_object_as_registry(self):
        rules = get_archetype("semiconductor_foundry")
        assert rules is ARCHETYPE_REGISTRY["semiconductor_foundry"]

    def test_get_archetype_unknown_id_returns_none(self):
        assert get_archetype("unknown_archetype_xyz") is None

    def test_get_archetype_empty_string_returns_none(self):
        assert get_archetype("") is None

    def test_get_archetype_all_known_ids_succeed(self):
        for archetype_id in _EXPECTED_ARCHETYPE_IDS:
            rules = get_archetype(archetype_id)
            assert rules is not None, f"get_archetype('{archetype_id}') returned None"


# ---------------------------------------------------------------------------
# TestTickerArchetypeMap
# ---------------------------------------------------------------------------

class TestTickerArchetypeMap:
    def test_nvda_maps_to_fabless_chip_designer(self):
        rules = get_ticker_archetype("NVDA")
        assert rules is not None
        assert rules.archetype_id == "fabless_ai_chip_designer"

    def test_asml_maps_to_semiconductor_equipment_supplier(self):
        rules = get_ticker_archetype("ASML")
        assert rules is not None
        assert rules.archetype_id == "semiconductor_equipment_supplier"

    def test_tsm_maps_to_semiconductor_foundry(self):
        rules = get_ticker_archetype("TSM")
        assert rules is not None
        assert rules.archetype_id == "semiconductor_foundry"

    def test_aapl_maps_to_consumer_electronics(self):
        rules = get_ticker_archetype("AAPL")
        assert rules is not None
        assert rules.archetype_id == "consumer_electronics"

    def test_lmt_maps_to_defense_contractor(self):
        rules = get_ticker_archetype("LMT")
        assert rules is not None
        assert rules.archetype_id == "defense_contractor"

    def test_rtx_maps_to_defense_contractor(self):
        rules = get_ticker_archetype("RTX")
        assert rules is not None
        assert rules.archetype_id == "defense_contractor"

    def test_unknown_ticker_returns_none(self):
        assert get_ticker_archetype("ZZZZ_FAKE") is None

    def test_lowercase_ticker_normalised_to_upper(self):
        assert get_ticker_archetype("nvda") is get_ticker_archetype("NVDA")
        assert get_ticker_archetype("asml") is get_ticker_archetype("ASML")
        assert get_ticker_archetype("tsm")  is get_ticker_archetype("TSM")

    def test_mixed_case_ticker_normalised(self):
        assert get_ticker_archetype("Aapl") is get_ticker_archetype("AAPL")

    def test_all_ticker_map_values_exist_in_registry(self):
        for ticker, archetype_id in TICKER_ARCHETYPE_MAP.items():
            assert archetype_id in ARCHETYPE_REGISTRY, (
                f"Ticker '{ticker}' maps to archetype_id '{archetype_id}' "
                f"which is not in ARCHETYPE_REGISTRY"
            )

    def test_ticker_map_covers_key_semiconductor_tickers(self):
        required = {"NVDA", "ASML", "TSM", "AAPL", "LMT", "RTX", "NOC"}
        for ticker in required:
            assert ticker in TICKER_ARCHETYPE_MAP, (
                f"Expected ticker '{ticker}' not in TICKER_ARCHETYPE_MAP"
            )


# ---------------------------------------------------------------------------
# TestArchetypeDistinctions (regression-prevention)
# ---------------------------------------------------------------------------

class TestArchetypeDistinctions:
    def test_asml_is_not_a_foundry(self):
        rules = get_ticker_archetype("ASML")
        assert rules is not None
        assert rules.archetype_id != "semiconductor_foundry", (
            "ASML should be semiconductor_equipment_supplier, not semiconductor_foundry"
        )

    def test_tsm_is_not_an_equipment_supplier(self):
        rules = get_ticker_archetype("TSM")
        assert rules is not None
        assert rules.archetype_id != "semiconductor_equipment_supplier", (
            "TSM should be semiconductor_foundry, not semiconductor_equipment_supplier"
        )

    def test_nvda_is_not_a_foundry(self):
        rules = get_ticker_archetype("NVDA")
        assert rules is not None
        assert rules.archetype_id != "semiconductor_foundry"

    def test_nvda_forbidden_patterns_include_production_capacity(self):
        rules = get_ticker_archetype("NVDA")
        assert rules is not None
        patterns_combined = " ".join(rules.forbidden_prose_patterns)
        assert "production capacity" in patterns_combined, (
            "NVDA archetype must forbid 'production capacity' language"
        )

    def test_nvda_forbidden_patterns_include_consumer_of_semiconductors(self):
        rules = get_ticker_archetype("NVDA")
        assert rules is not None
        patterns_combined = " ".join(rules.forbidden_prose_patterns)
        assert "consumer of semiconductors" in patterns_combined, (
            "NVDA archetype must forbid 'consumer of semiconductors' label"
        )

    def test_defense_contractor_deescalation_sensitivity_is_neutral(self):
        rules = get_archetype("defense_contractor")
        assert rules is not None
        deesc = rules.event_sensitivities.get("diplomatic_deescalation")
        assert deesc == "Neutral", (
            f"defense_contractor diplomatic_deescalation should be Neutral, got '{deesc}'"
        )

    def test_defense_contractor_conflict_sensitivity_is_bullish(self):
        rules = get_archetype("defense_contractor")
        assert rules is not None
        conflict = rules.event_sensitivities.get("military_conflict")
        assert conflict == "Bullish", (
            f"defense_contractor military_conflict should be Bullish, got '{conflict}'"
        )

    def test_equipment_supplier_forbidden_patterns_exclude_foundry_language(self):
        rules = get_archetype("semiconductor_equipment_supplier")
        assert rules is not None
        patterns_combined = " ".join(rules.forbidden_prose_patterns)
        assert "foundry" in patterns_combined or "semiconductor foundry" in patterns_combined, (
            "semiconductor_equipment_supplier must forbid foundry language"
        )

    def test_foundry_forbidden_patterns_exclude_equipment_language(self):
        rules = get_archetype("semiconductor_foundry")
        assert rules is not None
        patterns_combined = " ".join(rules.forbidden_prose_patterns)
        assert "EUV" in patterns_combined or "lithography" in patterns_combined, (
            "semiconductor_foundry must forbid EUV/lithography equipment language"
        )

    def test_foundry_and_equipment_are_distinct_archetypes(self):
        foundry = get_ticker_archetype("TSM")
        equipment = get_ticker_archetype("ASML")
        assert foundry is not None
        assert equipment is not None
        assert foundry.archetype_id != equipment.archetype_id
        assert foundry.display_name != equipment.display_name

    def test_commodity_producer_and_consumer_have_opposite_commodity_shock_sensitivities(self):
        producer = get_archetype("commodity_producer")
        consumer = get_archetype("commodity_consumer")
        assert producer is not None
        assert consumer is not None
        assert producer.event_sensitivities.get("commodity_shock") == "Bullish"
        assert consumer.event_sensitivities.get("commodity_shock") == "Bearish"

    def test_fabless_designer_deescalation_sensitivity_is_bullish(self):
        rules = get_archetype("fabless_ai_chip_designer")
        assert rules is not None
        assert rules.event_sensitivities.get("diplomatic_deescalation") == "Bullish"

    def test_defense_contractor_export_control_easing_is_neutral(self):
        rules = get_archetype("defense_contractor")
        assert rules is not None
        assert rules.event_sensitivities.get("export_control_easing") == "Neutral", (
            "Defense contractors do not benefit directly from export-control easing "
            "in the near term (3-5 year procurement cycles)"
        )
