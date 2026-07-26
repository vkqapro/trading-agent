"""Central configuration and logging utilities."""

from __future__ import annotations

import csv
import logging
import logging.handlers
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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
    enabled_strategies: Tuple[str, ...] = tuple(
        item.lower()
        for item in _csv_env(
            "ENABLED_STRATEGIES",
            "rebound,confirmed_breakout,false_breakout_one_bar",
        )
    )
    min_avg_volume: int = _env_int("MIN_AVG_VOLUME", 500000)
    lookback_bars: int = _env_int("LOOKBACK_BARS", 60)
    premarket_daily_lookback_days: int = _env_int("PREMARKET_DAILY_LOOKBACK_DAYS", 60)
    intraday_bar_duration: str = _env_str("INTRADAY_BAR_DURATION", "5 D")
    intraday_bar_size: str = _env_str("INTRADAY_BAR_SIZE", "5 mins")
    chart_daily_duration: str = _env_str("CHART_DAILY_DURATION", "2 Y")
    chart_weekly_duration: str = _env_str("CHART_WEEKLY_DURATION", "2 Y")
    chart_4h_duration: str = _env_str("CHART_4H_DURATION", "180 D")
    level_tolerance_pct: float = _env_float("LEVEL_TOLERANCE_PCT", 0.0025)
    consolidation_window: int = _env_int("CONSOLIDATION_WINDOW", 20)
    abnormal_range_multiplier: float = _env_float("ABNORMAL_RANGE_MULTIPLIER", 2.0)
    compression_range_multiplier: float = _env_float("COMPRESSION_RANGE_MULTIPLIER", 0.6)
    atr_travel_limit_pct: float = _env_float("ATR_TRAVEL_LIMIT_PCT", 0.75)
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
class InefficiencyReclaimSettings:
    """Validated environment-facing settings for the isolated IRS subsystem."""

    enabled: bool = _env_bool("INEFFICIENCY_RECLAIM_ENABLED", False)
    trading_mode: str = _env_str("IRS_TRADING_MODE", "paper").lower()
    allow_live_trading: bool = _env_bool("ALLOW_LIVE_TRADING", False)
    version: str = _env_str("IRS_STRATEGY_VERSION", "1.0")
    database_path: Path = field(
        default_factory=lambda: _resolve_env_path(
            _env_str("IRS_DATABASE_PATH", "memory/inefficiency_reclaim.db")
        )
    )
    daily_history_years: int = _env_int("IRS_DAILY_HISTORY_YEARS", 10)
    min_daily_history_rows: int = _env_int("IRS_MIN_DAILY_HISTORY_ROWS", 1000)
    hourly_history_years: int = _env_int("IRS_HOURLY_HISTORY_YEARS", 3)
    fifteen_minute_history_years: int = _env_int("IRS_15M_HISTORY_YEARS", 2)
    five_minute_history_months: int = _env_int("IRS_5M_HISTORY_MONTHS", 12)
    one_minute_history_months: int = _env_int("IRS_1M_HISTORY_MONTHS", 6)
    history_request_delay_seconds: float = _env_float("IRS_HISTORY_REQUEST_DELAY_SECONDS", 2.0)
    history_timeout_retry_count: int = _env_int("IRS_HISTORY_TIMEOUT_RETRY_COUNT", 1)
    history_timeout_retry_delay_seconds: float = _env_float("IRS_HISTORY_TIMEOUT_RETRY_DELAY_SECONDS", 12.0)
    min_true_range_atr_multiple: float = _env_float("IRS_MIN_TR_ATR_MULTIPLE", 1.40)
    min_body_ratio: float = _env_float("IRS_MIN_BODY_RATIO", 0.65)
    min_close_location: float = _env_float("IRS_MIN_CLOSE_LOCATION", 0.75)
    min_directional_efficiency: float = _env_float("IRS_MIN_DIRECTIONAL_EFFICIENCY", 0.70)
    max_overlap_ratio: float = _env_float("IRS_MAX_OVERLAP_RATIO", 0.25)
    min_relative_volume: float = _env_float("IRS_MIN_RELATIVE_VOLUME", 1.30)
    min_zone_width_atr: float = _env_float("IRS_MIN_ZONE_WIDTH_ATR", 0.08)
    max_zone_width_atr: float = _env_float("IRS_MAX_ZONE_WIDTH_ATR", 0.60)
    minimum_display_score: float = _env_float("IRS_MINIMUM_DISPLAY_SCORE", 80.0)
    minimum_order_score: float = _env_float("IRS_MINIMUM_ORDER_SCORE", 85.0)
    risk_percent_per_trade: float = _env_float("IRS_RISK_PERCENT_PER_TRADE", 0.25)
    max_fixed_risk_cash: float = _env_float("IRS_MAX_FIXED_RISK_CASH", 1000.0)
    min_price: float = _env_float("IRS_MIN_PRICE", 5.0)
    max_price: float = _env_float("IRS_MAX_PRICE", 1000.0)
    min_average_daily_volume: int = _env_int("IRS_MIN_AVERAGE_DAILY_VOLUME", 750000)
    min_average_daily_dollar_volume: float = _env_float(
        "IRS_MIN_AVERAGE_DAILY_DOLLAR_VOLUME", 20000000.0
    )
    max_spread_percent: float = _env_float("IRS_MAX_SPREAD_PERCENT", 0.20)
    max_quote_age_seconds: int = _env_int("IRS_MAX_QUOTE_AGE_SECONDS", 5)

    def validate(self) -> None:
        if self.trading_mode != "paper":
            raise ValueError("IRS_TRADING_MODE must remain 'paper'.")
        if self.allow_live_trading:
            raise ValueError("ALLOW_LIVE_TRADING must remain false for IRS.")
        if not (0.0 < self.risk_percent_per_trade <= 1.0):
            raise ValueError("IRS_RISK_PERCENT_PER_TRADE must be in percent units (0, 1].")
        if not (0.0 <= self.min_zone_width_atr < self.max_zone_width_atr <= 1.0):
            raise ValueError("IRS zone-width limits are invalid.")
        if not (0.0 <= self.minimum_display_score <= self.minimum_order_score <= 100.0):
            raise ValueError("IRS score thresholds are invalid.")
        if min(
            self.daily_history_years,
            self.min_daily_history_rows,
            self.hourly_history_years,
            self.fifteen_minute_history_years,
            self.five_minute_history_months,
            self.one_minute_history_months,
        ) <= 0:
            raise ValueError("IRS historical horizons must be positive.")
        if min(
            self.history_request_delay_seconds,
            self.history_timeout_retry_count,
            self.history_timeout_retry_delay_seconds,
        ) < 0:
            raise ValueError("IRS history pacing settings must be non-negative.")

    def strategy_config(self):
        """Build the pure strategy config without making that module read env."""
        from src.strategy.inefficiency_reclaim import IRSConfig

        self.validate()
        config = IRSConfig(
            version=self.version,
            min_true_range_atr_multiple=Decimal(str(self.min_true_range_atr_multiple)),
            min_body_ratio=Decimal(str(self.min_body_ratio)),
            min_close_location=Decimal(str(self.min_close_location)),
            min_directional_efficiency=Decimal(str(self.min_directional_efficiency)),
            max_overlap_ratio=Decimal(str(self.max_overlap_ratio)),
            min_relative_volume=Decimal(str(self.min_relative_volume)),
            min_zone_width_atr=Decimal(str(self.min_zone_width_atr)),
            max_zone_width_atr=Decimal(str(self.max_zone_width_atr)),
            minimum_display_score=Decimal(str(self.minimum_display_score)),
            minimum_order_score=Decimal(str(self.minimum_order_score)),
            risk_percent_per_trade=Decimal(str(self.risk_percent_per_trade)) / Decimal("100"),
            max_fixed_risk_cash=Decimal(str(self.max_fixed_risk_cash)),
            min_price=Decimal(str(self.min_price)),
            max_price=Decimal(str(self.max_price)),
            min_average_daily_volume=self.min_average_daily_volume,
            min_average_daily_dollar_volume=Decimal(str(self.min_average_daily_dollar_volume)),
            max_spread_percent=Decimal(str(self.max_spread_percent)),
            max_quote_age_seconds=self.max_quote_age_seconds,
        )
        config.validate()
        return config


@dataclass(frozen=True)
class NasdaqDataConfig:
    """Optional Nasdaq market-data fallback for historical candles."""

    enabled: bool = _env_bool("NASDAQ_DATA_PROVIDER_ENABLED", False)
    data_link_api_key: str = _env_str("NASDAQ_DATA_LINK_API_KEY", _env_str("NDL_APIKEY", ""))
    data_link_base_url: str = _env_str("NASDAQ_DATA_LINK_BASE_URL", "https://data.nasdaq.com/api/v3")
    data_link_eod_database: str = _env_str("NASDAQ_DATA_LINK_EOD_DATABASE", "EOD")
    cloud_base_url: str = _env_str("NASDAQ_CLOUD_BASE_URL", "")
    cloud_client_id: str = _env_str("NASDAQ_CLOUD_CLIENT_ID", "")
    cloud_client_secret: str = _env_str("NASDAQ_CLOUD_CLIENT_SECRET", "")
    cloud_source: str = _env_str("NASDAQ_CLOUD_SOURCE", "CQT")
    cloud_offset: str = _env_str("NASDAQ_CLOUD_OFFSET", "delayed")
    request_timeout_seconds: int = _env_int("NASDAQ_REQUEST_TIMEOUT_SECONDS", 20)

    @property
    def data_link_configured(self) -> bool:
        return bool(self.data_link_api_key)

    @property
    def cloud_configured(self) -> bool:
        return bool(self.cloud_base_url and self.cloud_client_id and self.cloud_client_secret)


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
    market_data_collector_end_hour: int = _env_int("MARKET_DATA_COLLECTOR_END_HOUR", 17)
    market_data_collector_end_minute: int = _env_int("MARKET_DATA_COLLECTOR_END_MINUTE", 30)
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
    inefficiency_reclaim: InefficiencyReclaimSettings = field(default_factory=InefficiencyReclaimSettings)
    nasdaq_data: NasdaqDataConfig = field(default_factory=NasdaqDataConfig)
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

    def __post_init__(self) -> None:
        # Invalid IRS config must fail closed without weakening the rest of the
        # bot. The scanner reports this validation error and IRS stays disabled.
        try:
            self.inefficiency_reclaim.validate()
        except ValueError as exc:
            object.__setattr__(
                self,
                "inefficiency_reclaim",
                InefficiencyReclaimSettings(
                    enabled=False,
                    trading_mode="paper",
                    allow_live_trading=False,
                ),
            )
            logging.getLogger(__name__).error("IRS disabled by invalid configuration: %s", exc)

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


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that tolerates Windows multi-process log locks."""

    def doRollover(self) -> None:  # noqa: N802 - logging API method name
        try:
            super().doRollover()
        except PermissionError:
            # Another bot/dashboard process can briefly hold application.log on
            # Windows while this process decides it is time to rotate. Avoid
            # flooding stderr with logging tracebacks by switching this process
            # to its own overflow log instead of retrying the locked rollover on
            # every subsequent INFO line.
            if self.stream:
                self.stream.close()
                self.stream = None
            original = Path(self.baseFilename)
            self.baseFilename = str(original.with_name(f"{original.stem}.{os.getpid()}.log"))
            if not self.delay:
                self.stream = self._open()


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

    file_handler = SafeRotatingFileHandler(
        SETTINGS.paths.runtime_dir / "application.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
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
