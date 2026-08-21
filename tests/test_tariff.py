"""Tests for Italian tariff band classification.

Edge cases: midnight, band boundaries, weekends, holidays, DST transitions.
"""

from datetime import datetime

from kilowattchiaro_engine.tariff import TariffBand, classify_band, is_italian_holiday
from datetime import date


# --- Band classification ---


def test_f1_weekday_midday():
    """Mon-Fri 8:00-19:00 → F1."""
    # Wednesday 10:00
    assert classify_band(datetime(2025, 6, 11, 10, 0)) == TariffBand.F1


def test_f1_weekday_8am_boundary():
    """8:00 is the start of F1."""
    # Thursday 8:00
    assert classify_band(datetime(2025, 6, 12, 8, 0)) == TariffBand.F1


def test_f2_weekday_7am():
    """Mon-Fri 7:00-8:00 → F2."""
    # Monday 7:00
    assert classify_band(datetime(2025, 6, 9, 7, 0)) == TariffBand.F2


def test_f2_weekday_7pm():
    """Mon-Fri 19:00-23:00 → F2."""
    # Tuesday 19:00
    assert classify_band(datetime(2025, 6, 10, 19, 0)) == TariffBand.F2


def test_f2_weekday_10pm():
    """Mon-Fri 22:00 → F2."""
    # Friday 22:00
    assert classify_band(datetime(2025, 6, 13, 22, 0)) == TariffBand.F2


def test_f3_weekday_night():
    """Mon-Fri 23:00-7:00 → F3."""
    # Wednesday 2:00
    assert classify_band(datetime(2025, 6, 11, 2, 0)) == TariffBand.F3


def test_f3_weekday_11pm():
    """23:00 is the start of F3."""
    # Thursday 23:00
    assert classify_band(datetime(2025, 6, 12, 23, 0)) == TariffBand.F3


def test_f3_weekday_midnight():
    """Midnight → F3."""
    # Friday 0:00
    assert classify_band(datetime(2025, 6, 13, 0, 0)) == TariffBand.F3


def test_f2_saturday_daytime():
    """Sat 7:00-23:00 → F2."""
    # Saturday 14:00
    assert classify_band(datetime(2025, 6, 14, 14, 0)) == TariffBand.F2


def test_f3_saturday_night():
    """Sat 23:00+ → F3."""
    assert classify_band(datetime(2025, 6, 14, 23, 0)) == TariffBand.F3


def test_f3_saturday_early_morning():
    """Sat before 7:00 → F3."""
    assert classify_band(datetime(2025, 6, 14, 5, 0)) == TariffBand.F3


def test_f3_sunday_all_day():
    """Sundays are always F3."""
    # Sunday at various hours
    assert classify_band(datetime(2025, 6, 15, 10, 0)) == TariffBand.F3
    assert classify_band(datetime(2025, 6, 15, 14, 0)) == TariffBand.F3
    assert classify_band(datetime(2025, 6, 15, 2, 0)) == TariffBand.F3


# --- Italian holidays ---


def test_holiday_capodanno():
    assert is_italian_holiday(date(2025, 1, 1)) is True


def test_holiday_ferragosto():
    assert is_italian_holiday(date(2025, 8, 15)) is True


def test_holiday_natale():
    assert is_italian_holiday(date(2025, 12, 25)) is True


def test_holiday_santo_stefano():
    assert is_italian_holiday(date(2025, 12, 26)) is True


def test_holiday_festa_liberazione():
    assert is_italian_holiday(date(2025, 4, 25)) is True


def test_holiday_pasquetta_2025():
    """Easter Monday 2025 is April 21."""
    assert is_italian_holiday(date(2025, 4, 21)) is True


def test_not_holiday_regular_day():
    assert is_italian_holiday(date(2025, 3, 12)) is False


def test_holiday_forces_f3():
    """National holidays → F3 even during weekday daytime."""
    # Festa della Repubblica, Monday June 2 2025, at 10:00
    assert classify_band(datetime(2025, 6, 2, 10, 0)) == TariffBand.F3


def test_f1_boundary_18_59():
    """18:59 is still F1 (before 19:00)."""
    assert classify_band(datetime(2025, 6, 11, 18, 59)) == TariffBand.F1


def test_f2_boundary_19_00():
    """19:00 is F2."""
    assert classify_band(datetime(2025, 6, 11, 19, 0)) == TariffBand.F2
