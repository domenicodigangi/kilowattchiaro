"""Tests for electricity price component decomposition models.

Covers: validation, component arithmetic, Italian defaults plausibility,
scenario projection, fan chart generation, and edge cases.
"""

import pytest
from pydantic import ValidationError

from kilowattchiaro_engine.models.price_components import (
    ITALIAN_DEFAULTS_2025,
    ComponentEscalationRates,
    ElectricityPriceComponents,
    FanChartYear,
    PriceProjectionResult,
    PriceScenario,
    build_default_scenarios,
    build_fan_chart,
    build_projection,
)


# ---------------------------------------------------------------------------
# ElectricityPriceComponents
# ---------------------------------------------------------------------------


class TestElectricityPriceComponents:
    def test_total_is_sum_of_components(self):
        c = ElectricityPriceComponents(
            wholesale_eur_kwh=0.10,
            network_eur_kwh=0.05,
            system_charges_eur_kwh=0.02,
            taxes_eur_kwh=0.03,
            retail_margin_eur_kwh=0.01,
        )
        assert c.total_eur_kwh == pytest.approx(0.21)

    def test_shares_sum_to_100(self):
        c = ElectricityPriceComponents(
            wholesale_eur_kwh=0.10,
            network_eur_kwh=0.05,
            system_charges_eur_kwh=0.02,
            taxes_eur_kwh=0.03,
            retail_margin_eur_kwh=0.01,
        )
        shares = c.shares()
        total_pct = sum(shares.values())
        assert total_pct == pytest.approx(100.0, abs=1.0)

    def test_shares_zero_total_returns_zeros(self):
        c = ElectricityPriceComponents(
            wholesale_eur_kwh=0.0,
            network_eur_kwh=0.0,
            system_charges_eur_kwh=0.0,
            taxes_eur_kwh=0.0,
            retail_margin_eur_kwh=0.0,
        )
        shares = c.shares()
        assert all(v == 0.0 for v in shares.values())

    def test_json_roundtrip(self):
        c = ElectricityPriceComponents(
            wholesale_eur_kwh=0.14,
            network_eur_kwh=0.056,
            system_charges_eur_kwh=0.028,
            taxes_eur_kwh=0.034,
            retail_margin_eur_kwh=0.022,
        )
        json_str = c.model_dump_json()
        restored = ElectricityPriceComponents.model_validate_json(json_str)
        assert restored.total_eur_kwh == pytest.approx(c.total_eur_kwh)

    def test_computed_total_in_serialization(self):
        """total_eur_kwh should appear in serialized output."""
        c = ElectricityPriceComponents(
            wholesale_eur_kwh=0.10,
            network_eur_kwh=0.05,
            system_charges_eur_kwh=0.02,
            taxes_eur_kwh=0.03,
            retail_margin_eur_kwh=0.01,
        )
        data = c.model_dump()
        assert "total_eur_kwh" in data
        assert data["total_eur_kwh"] == pytest.approx(0.21)


# ---------------------------------------------------------------------------
# Italian defaults plausibility
# ---------------------------------------------------------------------------


class TestItalianDefaults:
    def test_total_in_plausible_range(self):
        """ARERA reference: domestico residente should be EUR 0.25-0.35/kWh."""
        total = ITALIAN_DEFAULTS_2025.total_eur_kwh
        assert 0.25 <= total <= 0.35, f"Total {total} outside plausible range"

    def test_component_shares_plausible(self):
        shares = ITALIAN_DEFAULTS_2025.shares()
        # Wholesale should be the largest component (>40%)
        assert shares["wholesale"] > 40
        # Network should be ~15-25%
        assert 15 <= shares["network"] <= 25
        # System charges ~5-15%
        assert 5 <= shares["system_charges"] <= 15
        # Taxes ~5-15%
        assert 5 <= shares["taxes"] <= 15
        # Retail margin ~3-12%
        assert 3 <= shares["retail_margin"] <= 12

    def test_shares_sum_within_tolerance(self):
        shares = ITALIAN_DEFAULTS_2025.shares()
        total_pct = sum(shares.values())
        assert total_pct == pytest.approx(100.0, abs=0.5)

    def test_accise_addizionali_match_taxes(self):
        """Tax component should be in range of accise + addizionali (ARERA Q1 2026: ~10% of total)."""
        assert 0.020 <= ITALIAN_DEFAULTS_2025.taxes_eur_kwh <= 0.040

    def test_all_components_positive(self):
        for field in [
            "wholesale_eur_kwh",
            "network_eur_kwh",
            "system_charges_eur_kwh",
            "taxes_eur_kwh",
            "retail_margin_eur_kwh",
        ]:
            assert getattr(ITALIAN_DEFAULTS_2025, field) > 0


# ---------------------------------------------------------------------------
# ComponentEscalationRates
# ---------------------------------------------------------------------------


class TestComponentEscalationRates:
    def test_creation(self):
        rates = ComponentEscalationRates(
            wholesale_annual_pct=0.02,
            network_annual_pct=0.015,
            system_charges_annual_pct=-0.01,
            taxes_annual_pct=0.01,
            retail_margin_annual_pct=0.005,
        )
        assert rates.wholesale_annual_pct == 0.02
        assert rates.system_charges_annual_pct == -0.01

    def test_negative_escalation_allowed(self):
        """Declining system charges (Conto Energia expiry) should be valid."""
        rates = ComponentEscalationRates(
            wholesale_annual_pct=-0.05,
            network_annual_pct=-0.02,
            system_charges_annual_pct=-0.10,
            taxes_annual_pct=-0.01,
            retail_margin_annual_pct=-0.03,
        )
        assert rates.system_charges_annual_pct == -0.10


# ---------------------------------------------------------------------------
# PriceScenario
# ---------------------------------------------------------------------------


def _make_scenario(
    name: str = "Test",
    base_year: int = 2025,
    years: int = 5,
    wholesale_pct: float = 0.02,
) -> PriceScenario:
    return PriceScenario(
        name=name,
        description="Test scenario",
        base_year=base_year,
        base_components=ITALIAN_DEFAULTS_2025,
        escalation=ComponentEscalationRates(
            wholesale_annual_pct=wholesale_pct,
            network_annual_pct=0.015,
            system_charges_annual_pct=-0.01,
            taxes_annual_pct=0.01,
            retail_margin_annual_pct=0.005,
        ),
        years=years,
    )


class TestPriceScenario:
    def test_project_length(self):
        """Project returns years + 1 entries (including base year)."""
        scenario = _make_scenario(years=5)
        projection = scenario.project()
        assert len(projection) == 6  # base year + 5 years

    def test_project_first_entry_is_base(self):
        scenario = _make_scenario()
        projection = scenario.project()
        assert projection[0].total_eur_kwh == ITALIAN_DEFAULTS_2025.total_eur_kwh

    def test_project_wholesale_increases(self):
        """With positive wholesale escalation, wholesale should increase each year."""
        scenario = _make_scenario(wholesale_pct=0.03, years=10)
        projection = scenario.project()
        for i in range(1, len(projection)):
            assert projection[i].wholesale_eur_kwh > projection[i - 1].wholesale_eur_kwh

    def test_project_system_charges_decrease(self):
        """With negative system charges escalation, they should decrease."""
        scenario = _make_scenario(years=10)
        projection = scenario.project()
        for i in range(1, len(projection)):
            assert (
                projection[i].system_charges_eur_kwh
                <= projection[i - 1].system_charges_eur_kwh
            )

    def test_project_system_charges_floor_at_zero(self):
        """System charges should not go negative even with aggressive decline."""
        scenario = PriceScenario(
            name="Aggressive decline",
            description="Test",
            base_year=2025,
            base_components=ElectricityPriceComponents(
                wholesale_eur_kwh=0.14,
                network_eur_kwh=0.056,
                system_charges_eur_kwh=0.001,  # Very small
                taxes_eur_kwh=0.034,
                retail_margin_eur_kwh=0.022,
            ),
            escalation=ComponentEscalationRates(
                wholesale_annual_pct=0.0,
                network_annual_pct=0.0,
                system_charges_annual_pct=-0.50,  # 50% annual decline
                taxes_annual_pct=0.0,
                retail_margin_annual_pct=0.0,
            ),
            years=20,
        )
        projection = scenario.project()
        for entry in projection:
            assert entry.system_charges_eur_kwh >= 0.0

    def test_project_20_year_total_increases_base_scenario(self):
        """In the base scenario, total price should increase over 20 years."""
        scenario = _make_scenario(years=20)
        projection = scenario.project()
        assert projection[-1].total_eur_kwh > projection[0].total_eur_kwh

    def test_project_compound_growth_accuracy(self):
        """Verify compound growth formula: component * (1 + rate)^n."""
        scenario = _make_scenario(wholesale_pct=0.05, years=10)
        projection = scenario.project()
        expected_year_10 = round(
            ITALIAN_DEFAULTS_2025.wholesale_eur_kwh * (1.05**10), 6
        )
        assert projection[10].wholesale_eur_kwh == pytest.approx(
            expected_year_10, rel=1e-4
        )

    def test_years_validation(self):
        with pytest.raises(ValidationError):
            _make_scenario(years=0)

        with pytest.raises(ValidationError):
            _make_scenario(years=31)


# ---------------------------------------------------------------------------
# Fan chart
# ---------------------------------------------------------------------------


class TestFanChart:
    def test_fan_chart_year_ordering_valid(self):
        """Valid fan chart year should pass validation."""
        FanChartYear(
            year=2025,
            p10_eur_kwh=0.20,
            p25_eur_kwh=0.25,
            p50_eur_kwh=0.28,
            p75_eur_kwh=0.32,
            p90_eur_kwh=0.38,
        )

    def test_fan_chart_ordering_violation_rejected(self):
        """PriceProjectionResult should reject mis-ordered percentiles."""
        bad_chart = [
            FanChartYear(
                year=2025,
                p10_eur_kwh=0.30,  # Higher than P50
                p25_eur_kwh=0.25,
                p50_eur_kwh=0.28,
                p75_eur_kwh=0.32,
                p90_eur_kwh=0.38,
            )
        ]
        with pytest.raises(ValidationError, match="percentiles not ordered"):
            PriceProjectionResult(
                scenarios=[],
                fan_chart=bad_chart,
                base_year=2025,
                projection_years=20,
            )

    def test_build_fan_chart_from_default_scenarios(self):
        scenarios = build_default_scenarios()
        chart = build_fan_chart(scenarios)

        # Should have 21 entries (base year + 20 years)
        assert len(chart) == 21

        # First year: all scenarios start from same base, so P10 == P90
        assert chart[0].p10_eur_kwh == chart[0].p90_eur_kwh

        # Last year: P90 > P10 (scenarios diverge)
        assert chart[-1].p90_eur_kwh > chart[-1].p10_eur_kwh

    def test_fan_chart_monotonic_divergence(self):
        """Fan chart spread should generally increase over time."""
        scenarios = build_default_scenarios()
        chart = build_fan_chart(scenarios)

        spread_first = chart[0].p90_eur_kwh - chart[0].p10_eur_kwh
        spread_last = chart[-1].p90_eur_kwh - chart[-1].p10_eur_kwh
        assert spread_last > spread_first

    def test_fan_chart_p50_between_bounds(self):
        scenarios = build_default_scenarios()
        chart = build_fan_chart(scenarios)
        for entry in chart:
            assert entry.p10_eur_kwh <= entry.p50_eur_kwh <= entry.p90_eur_kwh

    def test_build_fan_chart_empty_scenarios(self):
        assert build_fan_chart([]) == []

    def test_build_fan_chart_single_scenario(self):
        """With one scenario, all percentiles should be equal."""
        scenario = _make_scenario(years=5)
        chart = build_fan_chart([scenario])
        assert len(chart) == 6
        for entry in chart:
            assert entry.p10_eur_kwh == entry.p90_eur_kwh


# ---------------------------------------------------------------------------
# PriceProjectionResult
# ---------------------------------------------------------------------------


class TestPriceProjectionResult:
    def test_build_projection_defaults(self):
        result = build_projection()
        assert result.base_year == 2025
        assert result.projection_years == 20
        assert len(result.scenarios) == 3
        assert len(result.fan_chart) == 21

    def test_build_projection_custom_years(self):
        result = build_projection(base_year=2026, years=10)
        assert result.base_year == 2026
        assert result.projection_years == 10
        assert len(result.fan_chart) == 11
        assert result.fan_chart[0].year == 2026
        assert result.fan_chart[-1].year == 2036

    def test_scenario_names_match_defaults(self):
        result = build_projection()
        names = {s.name for s in result.scenarios}
        assert names == {"Base", "High Energy", "Low Energy"}

    def test_high_energy_exceeds_base_at_year_20(self):
        result = build_projection()
        base = next(s for s in result.scenarios if s.name == "Base")
        high = next(s for s in result.scenarios if s.name == "High Energy")

        base_final = base.project()[-1].total_eur_kwh
        high_final = high.project()[-1].total_eur_kwh
        assert high_final > base_final

    def test_low_energy_below_base_at_year_20(self):
        result = build_projection()
        base = next(s for s in result.scenarios if s.name == "Base")
        low = next(s for s in result.scenarios if s.name == "Low Energy")

        base_final = base.project()[-1].total_eur_kwh
        low_final = low.project()[-1].total_eur_kwh
        assert low_final < base_final

    def test_serialization_roundtrip(self):
        result = build_projection(years=5)
        json_str = result.model_dump_json()
        restored = PriceProjectionResult.model_validate_json(json_str)
        assert len(restored.scenarios) == 3
        assert len(restored.fan_chart) == 6
        assert restored.fan_chart[0].year == 2025
