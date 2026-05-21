"""Central configuration and logging utilities."""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_DIR = BASE_DIR / "memory"
LOG_DIR = MEMORY_DIR / "runtime"

load_dotenv(BASE_DIR / ".env", override=False)


def _strip_env_comment(value: str) -> str:
    normalized = value.strip()
    if " #" in normalized:
        normalized = normalized.split(" #", 1)[0].strip()
    if "\t#" in normalized:
        normalized = normalized.split("\t#", 1)[0].strip()
    return normalized


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = _strip_env_comment(raw)
    return cleaned or default


def _env_int(name: str, default: int) -> int:
    return int(_env_str(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env_str(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return _env_str(name, fallback).lower() == "true"


def _csv_env(name: str, default: str) -> List[str]:
    return [item.strip() for item in _env_str(name, default).split(",") if item.strip()]


def _csv_env_with_fallback(name: str, fallback_name: str, default: str) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        raw = os.getenv(fallback_name)
    return [item.strip() for item in _strip_env_comment(raw or default).split(",") if item.strip()]


def _resolve_env_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return BASE_DIR / candidate


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _csv_env_file_or_fallback(path_name: str, inline_name: str, fallback_name: str, default: str) -> List[str]:
    path_value = _env_str(path_name, "")
    if path_value:
        file_values = _load_symbol_csv(_resolve_env_path(path_value))
        if file_values:
            return file_values
    return _csv_env_with_fallback(inline_name, fallback_name, default)


def _load_symbol_csv(path: Path) -> List[str]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]

    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    symbol_index = None
    for candidate in ("symbol", "symbols", "ticker", "tickers", "stock_symbol", "stock_symbols"):
        if candidate in header:
            symbol_index = header.index(candidate)
            break

    values: List[str] = []
    data_rows = rows[1:] if symbol_index is not None else rows
    for row in data_rows:
        cleaned = [cell.strip() for cell in row]
        if not cleaned:
            continue
        first_cell = cleaned[0]
        if first_cell.startswith("#"):
            continue
        if symbol_index is not None:
            if symbol_index >= len(cleaned):
                continue
            symbol = cleaned[symbol_index]
            if symbol:
                values.append(symbol)
            continue
        values.append(first_cell)

    return _dedupe_preserve_order(values)


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def fx_pair_components(symbol: str) -> tuple[str, str] | None:
    normalized = normalize_symbol(symbol)
    if "." in normalized:
        base, quote = normalized.split(".", 1)
        if len(base) == 3 and len(quote) == 3 and base.isalpha() and quote.isalpha():
            return base, quote
    compact = normalized.replace(".", "")
    if len(compact) == 6 and compact.isalpha():
        return compact[:3], compact[3:]
    return None


@dataclass(frozen=True)
class BrokerConfig:
    host: str = _env_str("IBKR_HOST", "127.0.0.1")
    port: int = _env_int("IBKR_PORT", 7497)
    client_id: int = _env_int("IBKR_CLIENT_ID", 1)
    reconnect_retries: int = _env_int("IBKR_RECONNECT_RETRIES", 5)
    reconnect_delay_seconds: int = _env_int("IBKR_RECONNECT_DELAY_SECONDS", 5)
    market_data_timeout_seconds: int = _env_int("IBKR_MARKET_DATA_TIMEOUT_SECONDS", 10)
    startup_retry_window_seconds: int = _env_int("IBKR_STARTUP_RETRY_WINDOW_SECONDS", 300)
    startup_retry_delay_seconds: int = _env_int("IBKR_STARTUP_RETRY_DELAY_SECONDS", 30)
    market_session_lock_wait_seconds: int = _env_int("MARKET_SESSION_LOCK_WAIT_SECONDS", 1800)


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = _env_float("RISK_PER_TRADE", 0.01)
    max_daily_loss_pct: float = _env_float("MAX_DAILY_LOSS", 0.02)
    max_positions: int = _env_int("MAX_OPEN_POSITIONS", 5)
    max_open_risk_pct: float = _env_float("MAX_OPEN_RISK", 0.03)
    min_reward_risk_ratio: float = _env_float("MIN_REWARD_RISK", 3.0)
    max_spread_pct: float = _env_float("MAX_SPREAD_PCT", 0.003)
    max_position_value: float = _env_float("MAX_POSITION_VALUE", 25000.0)
    calculated_stop_pct: float = _env_float("CALCULATED_STOP_PCT", 0.0015)
    max_stop_vs_calculated_multiplier: float = _env_float("MAX_STOP_VS_CALCULATED_MULTIPLIER", 1.2)
    first_unstable_minutes: int = _env_int("FIRST_UNSTABLE_MINUTES", 5)


@dataclass(frozen=True)
class StrategyConfig:
    min_avg_volume: int = _env_int("MIN_AVG_VOLUME", 500000)
    lookback_bars: int = _env_int("LOOKBACK_BARS", 60)
    premarket_daily_lookback_days: int = _env_int("PREMARKET_DAILY_LOOKBACK_DAYS", 60)
    intraday_bar_duration: str = _env_str("INTRADAY_BAR_DURATION", "5 D")
    intraday_bar_size: str = _env_str("INTRADAY_BAR_SIZE", "5 mins")
    level_tolerance_pct: float = _env_float("LEVEL_TOLERANCE_PCT", 0.0025)
    consolidation_window: int = _env_int("CONSOLIDATION_WINDOW", 20)
    abnormal_range_multiplier: float = _env_float("ABNORMAL_RANGE_MULTIPLIER", 2.0)
    compression_range_multiplier: float = _env_float("COMPRESSION_RANGE_MULTIPLIER", 0.6)
    atr_travel_limit_pct: float = _env_float("ATR_TRAVEL_LIMIT_PCT", 0.8)
    minimum_technical_atr_pct: float = _env_float("MIN_TECHNICAL_ATR_PCT", 0.01)
    level_strength_threshold: float = _env_float("LEVEL_STRENGTH_THRESHOLD", 4.0)
    level_merge_tolerance_pct: float = _env_float("LEVEL_MERGE_TOLERANCE_PCT", 0.0015)
    level_merge_min_dollars: float = _env_float("LEVEL_MERGE_MIN_DOLLARS", 0.05)
    level_zone_buffer_atr_pct: float = _env_float("LEVEL_ZONE_BUFFER_ATR_PCT", 0.12)
    level_merge_distance_atr_pct: float = _env_float("LEVEL_MERGE_DISTANCE_ATR_PCT", 0.25)
    level_ultra_close_atr_pct: float = _env_float("LEVEL_ULTRA_CLOSE_ATR_PCT", 0.08)
    max_level_zone_width_atr_pct: float = _env_float("MAX_LEVEL_ZONE_WIDTH_ATR_PCT", 0.5)
    min_clean_level_gap_atr_pct: float = _env_float("MIN_CLEAN_LEVEL_GAP_ATR_PCT", 1.5)
    min_trade_level_touches: int = _env_int("MIN_TRADE_LEVEL_TOUCHES", 3)


@dataclass(frozen=True)
class TradingHours:
    timezone: str = _env_str("TRADING_TIMEZONE", "America/New_York")
    market_open_hour: int = _env_int("MARKET_OPEN_HOUR", 9)
    market_open_minute: int = _env_int("MARKET_OPEN_MINUTE", 30)
    open_scan_start_hour: int = _env_int("OPEN_SCAN_START_HOUR", 9)
    open_scan_start_minute: int = _env_int("OPEN_SCAN_START_MINUTE", 35)
    open_scan_end_hour: int = _env_int("OPEN_SCAN_END_HOUR", 10)
    open_scan_end_minute: int = _env_int("OPEN_SCAN_END_MINUTE", 30)
    market_close_hour: int = _env_int("MARKET_CLOSE_HOUR", 16)
    market_close_minute: int = _env_int("MARKET_CLOSE_MINUTE", 0)
    no_new_entry_after_hour: int = _env_int("NO_NEW_ENTRY_AFTER_HOUR", 15)
    no_new_entry_after_minute: int = _env_int("NO_NEW_ENTRY_AFTER_MINUTE", 45)
    intraday_end_hour: int = _env_int("INTRADAY_END_HOUR", 15)
    intraday_end_minute: int = _env_int("INTRADAY_END_MINUTE", 45)


@dataclass(frozen=True)
class NewsConfig:
    api_key: str = _env_str("NEWS_API_KEY", "")
    base_url: str = _env_str("NEWS_API_BASE_URL", "https://eventregistry.org/api/v1/article/getArticles")
    macro_url: str = _env_str("NEWS_MACRO_URL", "https://eventregistry.org/api/v1/article/getArticles")
    earnings_url: str = _env_str("NEWS_EARNINGS_URL", "https://eventregistry.org/api/v1/article/getArticles")
    request_timeout_seconds: int = _env_int("NEWS_TIMEOUT_SECONDS", 10)
    ibkr_news_enabled: bool = _env_bool("IBKR_NEWS_ENABLED", True)
    ibkr_symbol_providers: Tuple[str, ...] = tuple(
        item for item in _csv_env("IBKR_NEWS_SYMBOL_PROVIDERS", "BRFUPDN,DJ-N") if item
    )
    ibkr_macro_providers: Tuple[str, ...] = tuple(
        item for item in _csv_env("IBKR_NEWS_MACRO_PROVIDERS", "BRFG,DJ-RTG") if item
    )
    ibkr_headline_limit: int = _env_int("IBKR_NEWS_HEADLINE_LIMIT", 10)
    ibkr_lookback_hours: int = _env_int("IBKR_NEWS_LOOKBACK_HOURS", 24)
    high_risk_keywords: Tuple[str, ...] = (
        "earnings",
        "lawsuit",
        "downgrade",
        "investigation",
        "fraud",
        "offering",
        "bankruptcy",
    )
    macro_risk_keywords: Tuple[str, ...] = (
        "cpi",
        "fomc",
        "fed speech",
        "federal reserve",
        "powell",
        "geopolitical",
        "war",
        "sanctions",
    )
    critical_macro_keywords: Tuple[str, ...] = ("cpi", "fomc", "fed speech", "federal reserve", "powell")
    macro_min_match_count: int = _env_int("NEWS_MACRO_MIN_MATCH_COUNT", 2)
    default_macro_query: str = _env_str(
        "NEWS_MACRO_QUERY",
        "CPI OR FOMC OR \"Fed speech\" OR \"Federal Reserve\" OR geopolitical OR sanctions",
    )


@dataclass(frozen=True)
class PathsConfig:
    trade_log: Path = MEMORY_DIR / "TRADE_LOG.md"
    research_log: Path = MEMORY_DIR / "RESEARCH_LOG.md"
    levels_log: Path = MEMORY_DIR / "LEVELS_LOG.md"
    weekly_log: Path = MEMORY_DIR / "WEEKLY_LOG.md"
    weekly_review_log: Path = MEMORY_DIR / "WEEKLY_REVIEW.md"
    strategy_doc: Path = MEMORY_DIR / "TRADING_STRATEGY.md"
    reports_dir: Path = MEMORY_DIR / "reports"
    runtime_dir: Path = LOG_DIR
    state_file: Path = LOG_DIR / "state.json"


@dataclass(frozen=True)
class Settings:
    app_name: str = "ibkr-gerchik-bot"
    paper_trading: bool = _env_bool("PAPER_TRADING", True)
    dry_run_mode: bool = _env_bool("DRY_RUN_MODE", True)
    auto_git_push: bool = _env_bool("AUTO_GIT_PUSH", False)
    git_branch: str = _env_str("WORKFLOW_GIT_BRANCH", "Test")
    account_currency: str = _env_str("ACCOUNT_CURRENCY", "USD")
    stock_symbols: List[str] = field(
        default_factory=lambda: _csv_env_file_or_fallback(
            "STOCK_SYMBOLS_FILE",
            "STOCK_SYMBOLS",
            "BOT_SYMBOLS",
            "AAPL,MSFT,NVDA,AMD,TSLA,META,AMZN,NFLX",
        )
    )
    fx_symbols: List[str] = field(default_factory=lambda: _csv_env("FX_SYMBOLS", ""))
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trading_hours: TradingHours = field(default_factory=TradingHours)
    news: NewsConfig = field(default_factory=NewsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    slack_webhook: str = _env_str("SLACK_WEBHOOK", "")
    slack_bot_token: str = _env_str("SLACK_BOT_TOKEN", "")
    slack_channel: str = _env_str("SLACK_CHANNEL", "")
    slack_commands_enabled: bool = _env_bool("SLACK_COMMANDS_ENABLED", True)
    slack_command_prefix: str = _env_str("SLACK_COMMAND_PREFIX", "ibkr").strip().lower()
    slack_allowed_user_ids: List[str] = field(default_factory=lambda: _csv_env("SLACK_ALLOWED_USER_IDS", ""))
    premarket_levels_export_min_strength: float = _env_float("PREMARKET_LEVELS_EXPORT_MIN_STRENGTH", 7.0)

    @property
    def symbols(self) -> List[str]:
        merged: List[str] = []
        seen: set[str] = set()
        for symbol in [*self.stock_symbols, *self.fx_symbols]:
            normalized = normalize_symbol(symbol)
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
        return merged

    def symbol_security_type(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        if normalized in {normalize_symbol(item) for item in self.fx_symbols}:
            return "CASH"
        if fx_pair_components(normalized) is not None:
            return "CASH"
        return "STK"


SETTINGS = Settings()


def ensure_directories() -> None:
    """Create runtime and memory directories expected by the application."""
    SETTINGS.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure application logging once."""
    ensure_directories()
    logger = logging.getLogger(SETTINGS.app_name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(SETTINGS.paths.runtime_dir / "application.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


LOGGER = setup_logging()


def append_markdown_log(path: Path, heading: str, fields: Dict[str, object]) -> None:
    """Append a timestamped Markdown section to a log file."""
    ensure_directories()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {heading} ({datetime.now().isoformat(timespec='seconds')})\n")
        for key, value in fields.items():
            handle.write(f"- **{key}**: {value}\n")
