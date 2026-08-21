"""Tests for historical Italian solar incentive regimes dataset.

Covers: regime model validation, date-to-regime lookup, boundary cases,
export mechanism, PV cost curve, electricity price curve, and edge cases.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from kilowattchiaro_engine.incentive_regimes import (
    ELECTRICITY_PRICE_EUR_PER_KWH,
    INCENTIVE_REGIMES,
    PV_COST_EUR_PER_KWP,
    ExportMechanism,
    IncentiveRegime,
    IncentiveType,
    get_electricity_price_at_year,
    get_export_mechanism_at_date,
    get_pv_cost_at_year,
    get_regime_at_date,
)


# ---------------------------------------------------------------------------
# IncentiveRegime model
# ---------------------------------------------------------------------------


class TestIncentiveRegimeModel:
    def test_feed_in_tariff_creation(self):
        regime = IncentiveRegime(
            name="Test FiT",
            start_date=date(2010, 1, 1),
            end_date=date(2012, 12, 31),
            incentive_type=IncentiveType.FEED_IN_TARIFF,
            rate_eur_kwh=0.35,
            duration_years=20,
        )
        assert regime.rate_eur_kwh == 0.35
        assert regime.deduction_percent is None
        assert regime.compatible_export == []

    def test_tax_deduction_creation(self):
        regime = IncentiveRegime(
            name="Test Deduction",
            start_date=date(2013, 1, 1),
            incentive_type=IncentiveType.TAX_DEDUCTION,
            deduction_percent=50.0,
            max_amount_eur=96_000.0,
            duration_years=10,
            compatible_export=[ExportMechanism.SSP, ExportMechanism.RID],
        )
        assert regime.end_date is None
        assert regime.deduction_percent == 50.0
        assert regime.max_amount_eur == 96_000.0

    def test_duration_must_be_positive(self):
        with pytest.raises(ValidationError):
            IncentiveRegime(
                name="Bad",
                start_date=date(2020, 1, 1),
                incentive_type=IncentiveType.TAX_DEDUCTION,
                duration_years=0,
            )

    def test_json_roundtrip(self):
        regime = INCENTIVE_REGIMES[0]
        json_str = regime.model_dump_json()
        restored = IncentiveRegime.model_validate_json(json_str)
        assert restored.name == regime.name
        assert restored.start_date == regime.start_date
        assert restored.rate_eur_kwh == regime.rate_eur_kwh

    def test_enum_values(self):
        assert IncentiveType.FEED_IN_TARIFF.value == "feed_in_tariff"
        assert IncentiveType.TAX_DEDUCTION.value == "tax_deduction"
        assert IncentiveType.SUPERBONUS.value == "superbonus"
        assert ExportMechanism.SSP.value == "SSP"
        assert ExportMechanism.RID.value == "RID"


# ---------------------------------------------------------------------------
# Static data: 7 regimes encoded
# ---------------------------------------------------------------------------


class TestIncentiveRegimesData:
    def test_seven_regimes_encoded(self):
        assert len(INCENTIVE_REGIMES) == 7

    def test_chronological_order(self):
        for i in range(1, len(INCENTIVE_REGIMES)):
            assert INCENTIVE_REGIMES[i].start_date >= INCENTIVE_REGIMES[i - 1].start_date

    def test_all_conto_energia_are_feed_in_tariff(self):
        for regime in INCENTIVE_REGIMES[:5]:
            assert regime.incentive_type == IncentiveType.FEED_IN_TARIFF
            assert regime.rate_eur_kwh is not None
            assert regime.duration_years == 20

    def test_detrazione_is_tax_deduction(self):
        det = INCENTIVE_REGIMES[5]
        assert det.name == "Detrazione 50%"
        assert det.incentive_type == IncentiveType.TAX_DEDUCTION
        assert det.deduction_percent == 50.0
        assert det.max_amount_eur == 96_000.0
        assert det.end_date is None

    def test_superbonus_is_superbonus_type(self):
        sb = INCENTIVE_REGIMES[6]
        assert sb.name == "Superbonus 110%"
        assert sb.incentive_type == IncentiveType.SUPERBONUS
        assert sb.deduction_percent == 110.0


# ---------------------------------------------------------------------------
# get_regime_at_date
# ---------------------------------------------------------------------------


class TestGetRegimeAtDate:
    """Acceptance criteria from the ticket + boundary cases."""

    def test_2015_06_01_returns_detrazione(self):
        regime = get_regime_at_date(date(2015, 6, 1))
        assert regime is not None
        assert regime.name == "Detrazione 50%"

    def test_2011_03_01_returns_conto_energia_iii(self):
        regime = get_regime_at_date(date(2011, 3, 1))
        assert regime is not None
        assert regime.name == "Conto Energia III"

    def test_2006_01_01_returns_conto_energia_i(self):
        regime = get_regime_at_date(date(2006, 1, 1))
        assert regime is not None
        assert regime.name == "Conto Energia I"

    def test_superbonus_overlap_priority(self):
        """During Jul 2020 - Dec 2023, Superbonus should win over Detrazione."""
        regime = get_regime_at_date(date(2021, 6, 1))
        assert regime is not None
        assert regime.name == "Superbonus 110%"

    def test_pre_incentive_returns_none(self):
        assert get_regime_at_date(date(2004, 1, 1)) is None

    def test_2026_returns_detrazione(self):
        """Detrazione 50% is still active (end_date=None)."""
        regime = get_regime_at_date(date(2026, 1, 1))
        assert regime is not None
        assert regime.name == "Detrazione 50%"

    def test_gap_between_ce_i_and_ce_ii_returns_none(self):
        """Mar 2007: no incentive available between CE I and CE II."""
        assert get_regime_at_date(date(2007, 3, 15)) is None

    # Boundary: first day of each regime
    def test_boundary_first_day_ce_i(self):
        regime = get_regime_at_date(date(2005, 9, 19))
        assert regime is not None
        assert regime.name == "Conto Energia I"

    def test_boundary_last_day_ce_i(self):
        regime = get_regime_at_date(date(2007, 2, 28))
        assert regime is not None
        assert regime.name == "Conto Energia I"

    def test_boundary_first_day_ce_ii(self):
        regime = get_regime_at_date(date(2007, 4, 1))
        assert regime is not None
        assert regime.name == "Conto Energia II"

    def test_boundary_ce_iv_starts_after_ce_iii(self):
        """CE III ends May 31, CE IV starts June 1 — no gap."""
        regime_may31 = get_regime_at_date(date(2011, 5, 31))
        regime_jun1 = get_regime_at_date(date(2011, 6, 1))
        assert regime_may31 is not None
        assert regime_jun1 is not None
        assert regime_may31.name == "Conto Energia III"
        assert regime_jun1.name == "Conto Energia IV"

    def test_boundary_ce_v_last_day(self):
        regime = get_regime_at_date(date(2013, 7, 6))
        assert regime is not None
        assert regime.name == "Conto Energia V"

    def test_boundary_detrazione_first_day(self):
        regime = get_regime_at_date(date(2013, 7, 7))
        assert regime is not None
        assert regime.name == "Detrazione 50%"

    def test_boundary_superbonus_first_day(self):
        regime = get_regime_at_date(date(2020, 7, 1))
        assert regime is not None
        assert regime.name == "Superbonus 110%"

    def test_boundary_superbonus_last_day(self):
        regime = get_regime_at_date(date(2023, 12, 31))
        assert regime is not None
        assert regime.name == "Superbonus 110%"

    def test_day_after_superbonus_returns_detrazione(self):
        regime = get_regime_at_date(date(2024, 1, 1))
        assert regime is not None
        assert regime.name == "Detrazione 50%"


# ---------------------------------------------------------------------------
# get_export_mechanism_at_date
# ---------------------------------------------------------------------------


class TestGetExportMechanism:
    def test_ssp_before_closure(self):
        assert get_export_mechanism_at_date(date(2020, 1, 1)) == ExportMechanism.SSP

    def test_rid_from_closure_date(self):
        assert get_export_mechanism_at_date(date(2025, 5, 1)) == ExportMechanism.RID

    def test_rid_after_closure(self):
        assert get_export_mechanism_at_date(date(2026, 1, 1)) == ExportMechanism.RID

    def test_ssp_day_before_closure(self):
        assert get_export_mechanism_at_date(date(2025, 4, 30)) == ExportMechanism.SSP


# ---------------------------------------------------------------------------
# PV cost curve
# ---------------------------------------------------------------------------


class TestPVCostCurve:
    def test_2005_is_6000(self):
        assert get_pv_cost_at_year(2005) == 6000

    def test_2025_is_2200(self):
        assert get_pv_cost_at_year(2025) == 2200

    def test_2010_value(self):
        assert get_pv_cost_at_year(2010) == 4200

    def test_supply_chain_uptick(self):
        """2021-2023 costs rose due to supply chain disruptions."""
        assert get_pv_cost_at_year(2021) > get_pv_cost_at_year(2020)
        assert get_pv_cost_at_year(2022) > get_pv_cost_at_year(2021)

    def test_overall_decline(self):
        assert get_pv_cost_at_year(2025) < get_pv_cost_at_year(2005)

    def test_clamp_below_2005(self):
        assert get_pv_cost_at_year(2000) == get_pv_cost_at_year(2005)

    def test_clamp_above_2025(self):
        assert get_pv_cost_at_year(2030) == get_pv_cost_at_year(2025)

    def test_all_years_present(self):
        for year in range(2005, 2026):
            assert year in PV_COST_EUR_PER_KWP


# ---------------------------------------------------------------------------
# Electricity price curve
# ---------------------------------------------------------------------------


class TestElectricityPriceCurve:
    def test_2010_is_019(self):
        assert get_electricity_price_at_year(2010) == pytest.approx(0.19)

    def test_2022_energy_crisis(self):
        assert get_electricity_price_at_year(2022) == pytest.approx(0.34)

    def test_2025_value(self):
        assert get_electricity_price_at_year(2025) == pytest.approx(0.28)

    def test_crisis_spike(self):
        """2022 was the energy crisis peak."""
        assert get_electricity_price_at_year(2022) > get_electricity_price_at_year(2021)
        assert get_electricity_price_at_year(2022) > get_electricity_price_at_year(2023)

    def test_clamp_below_2005(self):
        assert get_electricity_price_at_year(2000) == get_electricity_price_at_year(2005)

    def test_clamp_above_2025(self):
        assert get_electricity_price_at_year(2030) == get_electricity_price_at_year(2025)

    def test_all_years_present(self):
        for year in range(2005, 2026):
            assert year in ELECTRICITY_PRICE_EUR_PER_KWH
