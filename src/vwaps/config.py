"""Read and validate config.toml."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from vwaps.tenors import parse_tenor

# Best neighbouring market for each market (month-shape error, research/donor_choice.py).
DEFAULT_DONORS = {
    "AT": ("CH", "NL", "DE"),
    "BE": ("DE", "NL"),
    "CH": ("AT", "DE"),
    "DE": ("NL", "AT"),
    "ES": ("FR", "NL"),
    "FR": ("NL", "DE"),
    "IT": ("BE", "DE"),
    "NL": ("DE", "AT"),
}


@dataclass
class Config:
    base_dir: Path
    vwap_input: str
    mapping_file: Path
    eex_curves_dir: Path
    output_dir: Path
    # Layers
    layer_arbitrage: bool
    # EEX
    max_stale_days: int | None
    warn_stale_days: int
    timezones: dict[str, str]
    tenors: list[str]
    day_convention: str
    weekend_offset: int
    # Anchors
    min_volume: float
    max_anchor_dev: float
    warmup_days: int
    vwap_columns: dict[str, str]
    # Neighbour method (see ALGORITMO.md)
    tau_log: float = 0.7  # reach along the curve, in log days to delivery
    shrink_k: float = 1.0  # evidence needed before trusting nearby deviations
    volume_weight: float = 0.5  # anchor weight x (volume / median volume) ** volume_weight; 0 = off
    history_days: int = 0  # 0 = off; else last N input days fill the trust today lacks
    # Last fallback for contracts EEX does not quote (see fallback.py)
    fallback_enabled: bool = True
    # Without any own information the official EEX price is never published as is:
    # EWMA of the reference over the last N allowed publications.
    no_info_window: int = 2
    no_info_halflife: float = 0.5
    # Targets: produce a tenor only if this market's EEX offers it (see fill.py)
    only_eex_offered: bool = True
    offered_lookback: int = 20  # EEX publications checked for "offered"
    fallback_window: int = 20  # EEX publications in the sibling-shape EWMA
    fallback_halflife: float = 8.0  # EWMA half-life, in publications
    fallback_donors: tuple = ("DE", "FR", "AT")  # neighbouring markets, in order of preference
    # Best neighbour for each market, measured on month shapes (research/donor_choice.py)
    fallback_donors_by_area: dict = field(default_factory=lambda: {k: tuple(v) for k, v in DEFAULT_DONORS.items()})
    eex_offset_days: int = 0
    configuration_mode: str = "global"
    command_overrides: dict = field(default_factory=dict)

    def tz(self, area: str) -> str:
        return self.timezones.get(area, self.timezones.get("default", "Europe/Berlin"))

    def resolve(self, p: str | Path) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(str(p))))
        return p if p.is_absolute() else (self.base_dir / p)


def validate_method_config(cfg: Config) -> None:
    """Validate the two parameters of the neighbour method."""
    for name in ("tau_log", "shrink_k"):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"method.{name} must be a finite positive number")
    if cfg.tau_log < 0.05:
        raise ValueError("method.tau_log must be >= 0.05 (smaller values make every weight vanish)")
    vw = cfg.volume_weight
    if isinstance(vw, bool) or not isinstance(vw, (int, float)) or not math.isfinite(vw) or vw < 0 or vw > 2:
        raise ValueError("method.volume_weight must be a number between 0 and 2 (0 disables volume weighting)")
    if not isinstance(cfg.only_eex_offered, bool):
        raise ValueError("targets.only_eex_offered must be a boolean")
    if isinstance(cfg.offered_lookback, bool) or not isinstance(cfg.offered_lookback, int) or cfg.offered_lookback < 1:
        raise ValueError("targets.offered_lookback must be an integer >= 1")
    if isinstance(cfg.no_info_window, bool) or not isinstance(cfg.no_info_window, int) or cfg.no_info_window < 1:
        raise ValueError("fallback.no_info_window must be an integer >= 1")
    nih = cfg.no_info_halflife
    if isinstance(nih, bool) or not isinstance(nih, (int, float)) or not math.isfinite(nih) or nih <= 0:
        raise ValueError("fallback.no_info_halflife must be a finite positive number")
    if not isinstance(cfg.fallback_enabled, bool):
        raise ValueError("fallback.enabled must be a boolean")
    if isinstance(cfg.fallback_window, bool) or not isinstance(cfg.fallback_window, int) or cfg.fallback_window < 1:
        raise ValueError("fallback.window must be an integer >= 1")
    half = cfg.fallback_halflife
    if isinstance(half, bool) or not isinstance(half, (int, float)) or not math.isfinite(half) or half <= 0:
        raise ValueError("fallback.halflife must be a finite positive number")
    by_area = cfg.fallback_donors_by_area
    if not isinstance(by_area, dict) or any(
        not isinstance(k, str) or not isinstance(v, (list, tuple)) or any(not isinstance(a, str) or not a for a in v)
        for k, v in by_area.items()
    ):
        raise ValueError("fallback.donors_by_area must map area codes to lists of area codes")
    if not isinstance(cfg.fallback_donors, (list, tuple)) or any(not isinstance(a, str) or not a for a in cfg.fallback_donors):
        raise ValueError('fallback.donors must be a list of area codes such as ["DE", "FR"]')
    days = cfg.history_days
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("method.history_days must be an integer >= 0 (0 disables history)")


def validate_config(cfg: Config) -> None:
    """Validate user-controlled types and finite bounds before calculations."""
    # Import lazily because product_config also resolves and validates Config.
    from vwaps.product_config import validate_parameter_overrides

    validate_parameter_overrides(cfg.command_overrides)
    if not isinstance(cfg.layer_arbitrage, bool):
        raise ValueError("layers.arbitrage must be a boolean, not a quoted string or number")
    for name, value, choices in (
        ("run.configuration_mode", cfg.configuration_mode, ("global", "individual")),
        ("conventions.day", cfg.day_convention, ("calendar", "business")),
    ):
        if value not in choices:
            raise ValueError(f"{name} must be one of {choices}")
    if not isinstance(cfg.tenors, list) or any(not isinstance(label, str) for label in cfg.tenors):
        raise ValueError("targets.tenors must be a list of tenor strings")
    for label in cfg.tenors:
        parsed = parse_tenor(label)
        if parsed is None or (parsed[0] in ("Sum", "Win") and parsed[1] < 1):
            raise ValueError(f"targets.tenors contains an unsupported tenor: {label!r}")
    numbers = {
        "min_volume": ("method.min_volume", False),
        "max_anchor_dev": ("method.max_anchor_dev", False),
    }
    for attribute, (label, positive) in numbers.items():
        value = getattr(cfg, attribute)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (positive and value == 0)
        ):
            raise ValueError(f"{label} must be a finite {'positive' if positive else 'nonnegative'} number")
    for attribute, label, minimum in (
        ("warn_stale_days", "eex.warn_stale_days", 0),
        ("warmup_days", "run.warmup_days", 0),
        ("weekend_offset", "conventions.weekend_offset", None),
    ):
        value = getattr(cfg, attribute)
        if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
            raise ValueError(f"{label} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    if cfg.max_stale_days is not None and (
        isinstance(cfg.max_stale_days, bool) or not isinstance(cfg.max_stale_days, int) or cfg.max_stale_days < 0
    ):
        raise ValueError("eex.max_stale_days must be an integer >= 0 (0 means unlimited in TOML)")
    if isinstance(cfg.eex_offset_days, bool) or not isinstance(cfg.eex_offset_days, int) or cfg.eex_offset_days > 0:
        raise ValueError("eex.offset_days must be an integer <= 0 (calendar days)")
    for label, values in (("timezones", cfg.timezones), ("vwap_columns", cfg.vwap_columns)):
        if not isinstance(values, dict) or any(not isinstance(v, str) or not v.strip() for v in values.values()):
            raise ValueError(f"{label} must be a table of nonempty strings")
    validate_method_config(cfg)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for section in ("paths", "layers", "method", "fallback", "conventions", "eex", "targets", "timezones", "run", "vwap_columns"):
        if section in raw and not isinstance(raw[section], dict):
            raise ValueError(f"{section} must be a TOML table")
    paths = raw.get("paths", {})
    for name, value in paths.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"paths.{name} must be a nonempty path string")
    layers = raw.get("layers", {})
    method = raw.get("method", {})
    conv = raw.get("conventions", {})
    eex = raw.get("eex", {})
    cfg = Config(
        base_dir=path.parent,
        vwap_input=paths.get("vwap_input", "data/vwaps.csv"),
        mapping_file=Path(),
        eex_curves_dir=Path(),
        output_dir=Path(),
        layer_arbitrage=layers.get("arbitrage", False),
        max_stale_days=eex.get("max_stale_days", 0),
        warn_stale_days=eex.get("warn_stale_days", 3),
        eex_offset_days=eex.get("offset_days", 0),
        timezones=dict(raw.get("timezones", {"default": "Europe/Berlin"})),
        tenors=raw.get("targets", {}).get("tenors", []),
        only_eex_offered=raw.get("targets", {}).get("only_eex_offered", True),
        offered_lookback=raw.get("targets", {}).get("offered_lookback", 20),
        day_convention=conv.get("day", "calendar"),
        weekend_offset=conv.get("weekend_offset", 0),
        min_volume=method.get("min_volume", 0),
        max_anchor_dev=method.get("max_anchor_dev", 0),
        warmup_days=raw.get("run", {}).get("warmup_days", 0),
        configuration_mode=raw.get("run", {}).get("configuration_mode", "global"),
        vwap_columns=dict(raw.get("vwap_columns", {})),
        tau_log=method.get("tau_log", 0.7),
        shrink_k=method.get("shrink_k", 1.0),
        volume_weight=method.get("volume_weight", 0.5),
        history_days=method.get("history_days", 0),
        fallback_enabled=raw.get("fallback", {}).get("enabled", True),
        no_info_window=raw.get("fallback", {}).get("no_info_window", 2),
        no_info_halflife=raw.get("fallback", {}).get("no_info_halflife", 0.5),
        fallback_window=raw.get("fallback", {}).get("window", 20),
        fallback_halflife=raw.get("fallback", {}).get("halflife", 8.0),
        fallback_donors=tuple(raw.get("fallback", {}).get("donors", ("DE", "FR", "AT"))),
        fallback_donors_by_area={k: tuple(v) for k, v in raw.get("fallback", {}).get("donors_by_area", DEFAULT_DONORS).items()},
    )
    cfg.mapping_file = cfg.resolve(paths.get("mapping", "mappings/products.csv"))
    cfg.eex_curves_dir = cfg.resolve(paths.get("eex_curves_dir", "../eex_scraper/output/curves/POWER"))
    cfg.output_dir = cfg.resolve(paths.get("output_dir", "output"))
    validate_config(cfg)
    if cfg.max_stale_days == 0:
        cfg.max_stale_days = None
    return cfg
