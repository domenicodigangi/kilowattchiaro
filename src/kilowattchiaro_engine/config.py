"""Engine defaults — every assumption is a named, overridable setting.

Values mirror docs/methodology.md §12 (assumptions register). Override any
of them via environment variables prefixed ``KWC_ENGINE_`` (for example
``KWC_ENGINE_DEFAULT_ELECTRICITY_PRICE_EUR_KWH=0.28``) or by constructing
``EngineSettings`` explicitly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KWC_ENGINE_", extra="ignore")

    default_electricity_price_eur_kwh: float = 0.25  # Italian residential all-in rate
    default_panel_cost_eur_per_kwp: float = 2200.0  # turnkey installed cost per kWp
    default_battery_cost_eur: float = 8000.0  # 10 kWh LFP battery installed
    default_rid_price_eur_kwh: float = 0.0475  # GSE RID minimum guaranteed (PMG 2026)
    default_self_consumption_ratio: float = 0.30  # without battery
    default_self_consumption_ratio_battery: float = 0.70  # with battery
    default_maintenance_eur_yr: float = 75.0
    default_inverter_replacement_eur: float = 1750.0
    default_inverter_replacement_year: int = 11
    default_battery_replacement_year: int = 14  # modern LFP: ~16 yr to 80% SOH
    default_panel_degradation_pct: float = 0.005
    default_battery_fade_pct: float = 0.02


settings = EngineSettings()
