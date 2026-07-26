"""Pure implementation of the Inefficiency Reclaim Strategy (IRS).

The module deliberately has no broker, storage, alerting, or environment
dependencies.  Every function operates only on explicitly supplied,
completed market data and configuration.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Any, Mapping, Sequence


STRATEGY_CODE = "INEFFICIENCY_RECLAIM"
STRATEGY_VERSION = "1.0"
EXCHANGE_TIMEZONE = "America/New_York"
ZERO = Decimal("0")


class Direction(str, Enum):
    LONG = "IRS_LONG"
    SHORT = "IRS_SHORT"


class ZoneType(str, Enum):
    STRICT_THREE_BAR_GAP = "STRICT_THREE_BAR_GAP"
    LOW_OVERLAP_DISPLACEMENT = "LOW_OVERLAP_DISPLACEMENT"


class ConfirmationType(str, Enum):
    SWEEP_AND_RECLAIM = "SWEEP_AND_RECLAIM"
    MICRO_BREAK_OF_STRUCTURE = "MICRO_BREAK_OF_STRUCTURE"
    TWO_BAR_RECLAIM = "TWO_BAR_RECLAIM"


class StrategyProfile(str, Enum):
    INTRADAY = "intraday"
    SWING = "swing"


class SetupState(str, Enum):
    SEARCHING = "SEARCHING"
    DISPLACEMENT_FOUND = "DISPLACEMENT_FOUND"
    INEFFICIENCY_REGISTERED = "INEFFICIENCY_REGISTERED"
    WAITING_FOR_RETRACE = "WAITING_FOR_RETRACE"
    RETRACE_DETECTED = "RETRACE_DETECTED"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    ENTRY_ARMED = "ENTRY_ARMED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_CLOSED = "POSITION_CLOSED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    REJECTED_BY_DATA = "REJECTED_BY_DATA"
    REJECTED_BY_REGIME = "REJECTED_BY_REGIME"
    REJECTED_BY_NEWS = "REJECTED_BY_NEWS"
    REJECTED_BY_LIQUIDITY = "REJECTED_BY_LIQUIDITY"
    REJECTED_BY_TARGET_SPACE = "REJECTED_BY_TARGET_SPACE"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    CANCELLED = "CANCELLED"


class MarketRegime(str, Enum):
    TREND_LOW_VOL = "TREND_LOW_VOL"
    TREND_HIGH_VOL = "TREND_HIGH_VOL"
    RANGE_LOW_VOL = "RANGE_LOW_VOL"
    RANGE_HIGH_VOL = "RANGE_HIGH_VOL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    UNKNOWN = "UNKNOWN"


class RiskStatus(str, Enum):
    CLEAR = "CLEAR"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class RejectionReason(str, Enum):
    INSUFFICIENT_DAILY_HISTORY = "INSUFFICIENT_DAILY_HISTORY"
    INSUFFICIENT_HOURLY_HISTORY = "INSUFFICIENT_HOURLY_HISTORY"
    INSUFFICIENT_15M_HISTORY = "INSUFFICIENT_15M_HISTORY"
    INCOMPLETE_SETUP_BAR = "INCOMPLETE_SETUP_BAR"
    INVALID_BAR_DATA = "INVALID_BAR_DATA"
    STALE_QUOTE = "STALE_QUOTE"
    MISSING_QUOTE = "MISSING_QUOTE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"
    LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
    NO_MEANINGFUL_STRUCTURE = "NO_MEANINGFUL_STRUCTURE"
    DISPLACEMENT_TOO_SMALL = "DISPLACEMENT_TOO_SMALL"
    BODY_RATIO_TOO_LOW = "BODY_RATIO_TOO_LOW"
    CLOSE_LOCATION_TOO_WEAK = "CLOSE_LOCATION_TOO_WEAK"
    DIRECTIONAL_EFFICIENCY_TOO_LOW = "DIRECTIONAL_EFFICIENCY_TOO_LOW"
    OVERLAP_TOO_HIGH = "OVERLAP_TOO_HIGH"
    RELATIVE_VOLUME_TOO_LOW = "RELATIVE_VOLUME_TOO_LOW"
    ZONE_INVALID = "ZONE_INVALID"
    ZONE_TOO_NARROW = "ZONE_TOO_NARROW"
    ZONE_TOO_WIDE = "ZONE_TOO_WIDE"
    ZONE_EXPIRED = "ZONE_EXPIRED"
    RETRACE_TOO_SHALLOW = "RETRACE_TOO_SHALLOW"
    RETRACE_TOO_DEEP = "RETRACE_TOO_DEEP"
    PULLBACK_VOLUME_TOO_HIGH = "PULLBACK_VOLUME_TOO_HIGH"
    ZONE_ACCEPTED_AGAINST_TRADE = "ZONE_ACCEPTED_AGAINST_TRADE"
    CONFIRMATION_NOT_FOUND = "CONFIRMATION_NOT_FOUND"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    ENTRY_OVEREXTENDED = "ENTRY_OVEREXTENDED"
    INVALID_STOP = "INVALID_STOP"
    STOP_TOO_TIGHT = "STOP_TOO_TIGHT"
    STOP_TOO_WIDE = "STOP_TOO_WIDE"
    INSUFFICIENT_TARGET_SPACE = "INSUFFICIENT_TARGET_SPACE"
    SCORE_TOO_LOW = "SCORE_TOO_LOW"
    MARKET_REGIME_OPPOSED = "MARKET_REGIME_OPPOSED"
    SECTOR_REGIME_OPPOSED = "SECTOR_REGIME_OPPOSED"
    EARNINGS_BLOCK = "EARNINGS_BLOCK"
    NEWS_RISK = "NEWS_RISK"
    NEWS_STATUS_UNAVAILABLE = "NEWS_STATUS_UNAVAILABLE"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    PENDING_ORDER_EXISTS = "PENDING_ORDER_EXISTS"
    POSITION_EXISTS = "POSITION_EXISTS"
    DAILY_TRADE_LIMIT = "DAILY_TRADE_LIMIT"
    DAILY_LOSS_LOCKOUT = "DAILY_LOSS_LOCKOUT"
    MAX_POSITIONS = "MAX_POSITIONS"
    BUYING_POWER = "BUYING_POWER"
    LIVE_TRADING_DISABLED = "LIVE_TRADING_DISABLED"
    PROTECTIVE_STOP_UNAVAILABLE = "PROTECTIVE_STOP_UNAVAILABLE"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    SESSION_CUTOFF = "SESSION_CUTOFF"


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_complete: bool = True
    is_regular_session: bool = True
    adjustment_status: str = "adjusted"
    source: str = "stored"
    bar_id: str = ""

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar timestamp must be timezone-aware.")
        if self.volume < 0:
            raise ValueError("Bar volume cannot be negative.")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Bar high violates OHLC ordering.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Bar low violates OHLC ordering.")
        if not self.bar_id:
            token = f"{self.symbol}|{self.timeframe}|{self.timestamp.isoformat()}"
            object.__setattr__(self, "bar_id", hashlib.sha256(token.encode("utf-8")).hexdigest()[:20])

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def range(self) -> Decimal:
        return self.high - self.low


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Quote timestamp must be timezone-aware.")

    @property
    def mid(self) -> Decimal:
        if self.bid > ZERO and self.ask > ZERO:
            return (self.bid + self.ask) / Decimal("2")
        return self.last

    @property
    def spread_percent(self) -> Decimal | None:
        if self.bid <= ZERO or self.ask <= ZERO or self.mid <= ZERO:
            return None
        return (self.ask - self.bid) / self.mid * Decimal("100")


@dataclass(frozen=True)
class StructuralLevel:
    price: Decimal
    kind: str
    level_id: str = ""
    strength: Decimal = Decimal("0")


@dataclass(frozen=True)
class AccountState:
    equity: Decimal
    buying_power: Decimal
    open_positions: int = 0
    trades_today: int = 0
    daily_loss: Decimal = ZERO
    existing_symbols: tuple[str, ...] = ()
    pending_symbols: tuple[str, ...] = ()
    protective_stop_available: bool = True
    broker_connected: bool = True
    short_available: bool = True


@dataclass(frozen=True)
class IRSConfig:
    version: str = STRATEGY_VERSION
    profile: StrategyProfile = StrategyProfile.INTRADAY
    tick_size: Decimal = Decimal("0.01")
    robust_atr_lookback: int = 20
    robust_atr_mad_z_threshold: Decimal = Decimal("3.5")
    robust_atr_trim_percent: Decimal = Decimal("0.10")
    robust_atr_min_values: int = 12
    min_true_range_atr_multiple: Decimal = Decimal("1.40")
    min_body_ratio: Decimal = Decimal("0.65")
    min_close_location: Decimal = Decimal("0.75")
    min_directional_efficiency: Decimal = Decimal("0.70")
    max_overlap_ratio: Decimal = Decimal("0.25")
    min_relative_volume: Decimal = Decimal("1.30")
    min_zone_width_atr: Decimal = Decimal("0.08")
    max_zone_width_atr: Decimal = Decimal("0.60")
    min_retrace_wait_1h_bars: int = 1
    max_retrace_wait_1h_bars: int = 7
    max_confirmation_15m_bars: int = 8
    min_retrace_depth: Decimal = Decimal("0.25")
    preferred_retrace_min: Decimal = Decimal("0.40")
    preferred_retrace_max: Decimal = Decimal("0.70")
    max_retrace_depth: Decimal = Decimal("0.85")
    max_pullback_volume_ratio: Decimal = Decimal("0.75")
    max_pullback_relative_volume: Decimal = Decimal("1.10")
    min_sweep_penetration_atr: Decimal = Decimal("0.02")
    max_sweep_penetration_atr: Decimal = Decimal("0.30")
    min_reclaim_close_location: Decimal = Decimal("0.60")
    min_rejection_wick_ratio: Decimal = Decimal("0.25")
    entry_buffer_atr: Decimal = Decimal("0.02")
    entry_expiration_15m_bars: int = 2
    max_entry_extension_atr: Decimal = Decimal("0.30")
    stop_buffer_atr: Decimal = Decimal("0.08")
    min_stop_distance_atr: Decimal = Decimal("0.15")
    max_stop_distance_atr: Decimal = Decimal("1.10")
    hard_minimum_r: Decimal = Decimal("0.90")
    preferred_minimum_r: Decimal = Decimal("1.20")
    target_r: Decimal = Decimal("1.20")
    max_precision_r: Decimal = Decimal("1.50")
    minimum_display_score: Decimal = Decimal("80")
    minimum_order_score: Decimal = Decimal("85")
    risk_percent_per_trade: Decimal = Decimal("0.0025")
    max_fixed_risk_cash: Decimal = Decimal("1000")
    max_shares: int = 10000
    max_position_value: Decimal = Decimal("25000")
    min_price: Decimal = Decimal("5")
    max_price: Decimal = Decimal("1000")
    min_average_daily_volume: int = 750000
    min_average_daily_dollar_volume: Decimal = Decimal("20000000")
    max_spread_percent: Decimal = Decimal("0.20")
    max_quote_age_seconds: int = 5
    max_positions: int = 5
    max_trades_per_day: int = 5
    max_daily_loss_percent: Decimal = Decimal("0.02")
    commission_per_share: Decimal = Decimal("0.005")
    expected_slippage_per_share: Decimal = Decimal("0.01")

    def validate(self) -> None:
        unit_values = {
            "robust_atr_trim_percent": self.robust_atr_trim_percent,
            "min_body_ratio": self.min_body_ratio,
            "min_close_location": self.min_close_location,
            "min_directional_efficiency": self.min_directional_efficiency,
            "max_overlap_ratio": self.max_overlap_ratio,
            "min_zone_width_atr": self.min_zone_width_atr,
            "max_zone_width_atr": self.max_zone_width_atr,
            "min_retrace_depth": self.min_retrace_depth,
            "preferred_retrace_min": self.preferred_retrace_min,
            "preferred_retrace_max": self.preferred_retrace_max,
            "max_retrace_depth": self.max_retrace_depth,
        }
        for name, value in unit_values.items():
            if value < ZERO or value > Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1.")
        if not (
            self.min_retrace_depth
            <= self.preferred_retrace_min
            <= self.preferred_retrace_max
            <= self.max_retrace_depth
        ):
            raise ValueError("Retrace depth bands are not ordered.")
        if self.min_zone_width_atr >= self.max_zone_width_atr:
            raise ValueError("Zone width bounds are invalid.")
        if self.minimum_display_score > self.minimum_order_score:
            raise ValueError("Display score cannot exceed order score.")
        if self.tick_size <= ZERO or self.robust_atr_min_values < 2:
            raise ValueError("Tick size and ATR sample count must be positive.")


@dataclass(frozen=True)
class DisplacementResult:
    passed: bool
    direction: Direction | None
    metrics: Mapping[str, Any]
    failed_conditions: tuple[str, ...]
    structure_reference: str | None
    bar_id: str


@dataclass(frozen=True)
class InefficiencyZone:
    zone_id: str
    symbol: str
    direction: Direction
    zone_type: ZoneType
    source_timeframe: str
    created_at: datetime
    displacement_bar_time: datetime
    zone_low: Decimal
    zone_high: Decimal
    zone_mid: Decimal
    zone_width: Decimal
    zone_width_atr: Decimal
    displacement_atr_multiple: Decimal
    body_ratio: Decimal
    close_location: Decimal
    relative_volume: Decimal
    source_bar_ids: tuple[str, ...]
    structure_reference_id: str | None
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Zone datetimes must be timezone-aware.")
        if self.zone_low >= self.zone_high:
            raise ValueError("Zone low must be below zone high.")


@dataclass(frozen=True)
class RetraceResult:
    detected: bool
    depth: Decimal | None
    preferred: bool
    pullback_volume_ratio: Decimal | None
    pullback_relative_volume: Decimal | None
    invalidated: bool
    expired: bool
    reasons: tuple[str, ...]
    first_touch_time: datetime | None
    sweep_extreme: Decimal | None
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfirmationEvent:
    confirmation_type: ConfirmationType
    timestamp: datetime
    bar_id: str
    high: Decimal
    low: Decimal
    boundary: Decimal
    score: Decimal
    metrics: Mapping[str, Any]


@dataclass(frozen=True)
class ScoreBreakdown:
    structure: Decimal
    displacement: Decimal
    zone: Decimal
    volume_order_flow: Decimal
    retrace: Decimal
    confirmation: Decimal
    regime: Decimal
    target_space: Decimal

    @property
    def total(self) -> Decimal:
        return sum(
            (
                self.structure,
                self.displacement,
                self.zone,
                self.volume_order_flow,
                self.retrace,
                self.confirmation,
                self.regime,
                self.target_space,
            ),
            ZERO,
        ).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class OrderPlan:
    entry_stop: Decimal
    entry_limit: Decimal
    stop: Decimal
    target: Decimal
    risk_per_share: Decimal
    reward_per_share: Decimal
    structural_r: Decimal
    estimated_costs: Decimal
    quantity: int
    risk_cash: Decimal
    required_buying_power: Decimal
    order_type: str = "STOP_LIMIT"


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    as_of: datetime
    daily_bars: Sequence[Bar]
    hourly_bars: Sequence[Bar]
    bars_15m: Sequence[Bar]
    bars_5m: Sequence[Bar] | None = None
    bars_1m: Sequence[Bar] | None = None
    quote: Quote | None = None
    levels: Sequence[StructuralLevel] = ()
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    market_trend_direction: str | None = None
    sector_regime: MarketRegime | None = None
    sector_trend_direction: str | None = None
    relative_strength_20d: Decimal | None = None
    news_status: RiskStatus = RiskStatus.UNKNOWN
    earnings_status: RiskStatus = RiskStatus.UNKNOWN
    corporate_action_status: RiskStatus = RiskStatus.UNKNOWN
    halt_status: RiskStatus = RiskStatus.UNKNOWN
    account_state: AccountState | None = None
    session_entry_allowed: bool = False

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("Strategy as_of must be timezone-aware.")


@dataclass(frozen=True)
class StrategyCandidate:
    signal_id: str
    symbol: str
    strategy: str
    profile: StrategyProfile
    direction: Direction
    state: SetupState
    score: Decimal
    score_breakdown: ScoreBreakdown
    zone: InefficiencyZone
    displacement_metrics: Mapping[str, Any]
    retrace_metrics: Mapping[str, Any]
    confirmation: ConfirmationEvent | None
    order_plan: OrderPlan | None
    expires_at: datetime | None
    hard_rejections: tuple[str, ...]
    soft_warnings: tuple[str, ...]
    diagnostics: Mapping[str, Any]
    explanation: str
    strategy_version: str = STRATEGY_VERSION
    paper_live_mode: str = "PAPER"

    @property
    def setup_id(self) -> str:
        token = "|".join(
            (
                self.symbol.upper(),
                self.strategy,
                self.profile.value,
                self.zone.zone_id,
                self.direction.value,
            )
        )
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:28]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["setup_id"] = self.setup_id
        return _json_value(payload)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _decimal(value: Decimal | float | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return ZERO if denominator <= ZERO else numerator / denominator


def _bounded(value: Decimal, low: Decimal = ZERO, high: Decimal = Decimal("1")) -> Decimal:
    return min(high, max(low, value))


def completed_bars(bars: Sequence[Bar], as_of: datetime | None = None) -> tuple[Bar, ...]:
    """Return ordered, unique, completed RTH bars available at ``as_of``."""
    filtered = [
        bar
        for bar in bars
        if bar.is_complete and bar.is_regular_session and (as_of is None or bar.timestamp <= as_of)
    ]
    by_time = {bar.timestamp: bar for bar in filtered}
    return tuple(by_time[key] for key in sorted(by_time))


def calculate_true_range(bar: Bar, previous_close: Decimal | None) -> Decimal:
    values = [bar.high - bar.low]
    if previous_close is not None:
        values.extend((abs(bar.high - previous_close), abs(bar.low - previous_close)))
    return max(values)


def calculate_true_ranges(bars: Sequence[Bar]) -> tuple[Decimal, ...]:
    ordered = completed_bars(bars)
    values: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in ordered:
        values.append(calculate_true_range(bar, previous_close))
        previous_close = bar.close
    return tuple(values)


def calculate_robust_atr(
    bars: Sequence[Bar],
    config: IRSConfig = IRSConfig(),
    *,
    end_index: int | None = None,
) -> Decimal | None:
    """Calculate the median/MAD filtered and symmetrically trimmed ATR."""
    ordered = completed_bars(bars)
    if end_index is not None:
        ordered = ordered[:end_index]
    true_ranges = list(calculate_true_ranges(ordered)[-config.robust_atr_lookback :])
    true_ranges = [value for value in true_ranges if value > ZERO]
    if len(true_ranges) < config.robust_atr_min_values:
        return None

    floats = [float(value) for value in true_ranges]
    median = statistics.median(floats)
    deviations = [abs(value - median) for value in floats]
    mad = statistics.median(deviations)
    if mad > 0:
        filtered = [
            value
            for value in floats
            if abs(0.6745 * (value - median) / mad) <= float(config.robust_atr_mad_z_threshold)
        ]
    else:
        ordered_values = sorted(floats)
        lower_index = max(0, int(math.floor(0.05 * (len(ordered_values) - 1))))
        upper_index = min(len(ordered_values) - 1, int(math.ceil(0.95 * (len(ordered_values) - 1))))
        lower = ordered_values[lower_index]
        upper = ordered_values[upper_index]
        filtered = [min(upper, max(lower, value)) for value in floats]

    trim_count = int(len(filtered) * float(config.robust_atr_trim_percent))
    trimmed = sorted(filtered)
    if trim_count and len(trimmed) - 2 * trim_count >= config.robust_atr_min_values:
        trimmed = trimmed[trim_count:-trim_count]
    if len(trimmed) < config.robust_atr_min_values:
        ordered_values = sorted(floats)
        cut = max(1, int(len(ordered_values) * 0.10))
        lower = ordered_values[min(cut, len(ordered_values) - 1)]
        upper = ordered_values[max(0, len(ordered_values) - cut - 1)]
        trimmed = [min(upper, max(lower, value)) for value in ordered_values]
    if len(trimmed) < config.robust_atr_min_values:
        return None
    result = Decimal(str(statistics.fmean(trimmed)))
    return result if result > ZERO and result.is_finite() else None


def calculate_relative_volume(bars: Sequence[Bar], index: int, lookback: int = 20) -> Decimal | None:
    """Current volume divided by median prior volume; current is excluded."""
    if index < 0:
        index += len(bars)
    if index <= 0 or index >= len(bars):
        return None
    prior = [bar.volume for bar in bars[max(0, index - lookback) : index] if bar.is_complete]
    if len(prior) < min(5, lookback) or statistics.median(prior) <= 0:
        return None
    return Decimal(bars[index].volume) / Decimal(str(statistics.median(prior)))


def calculate_overlap_ratio(current: Bar, previous: Bar) -> Decimal:
    """Intersection of prior/current ranges divided by the current true range."""
    overlap = max(ZERO, min(current.high, previous.high) - max(current.low, previous.low))
    return _ratio(overlap, current.range)


def _structure_reference(
    bars: Sequence[Bar],
    index: int,
    direction: Direction,
    atr: Decimal,
    levels: Sequence[StructuralLevel],
    tick_size: Decimal,
) -> str | None:
    current = bars[index]
    buffer = max(tick_size * 2, atr * Decimal("0.05"))
    if direction is Direction.LONG:
        candidates = [
            level
            for level in levels
            if level.kind.lower() in {"resistance", "swing_high", "balance_high"}
            and current.close > level.price + buffer
        ]
        if candidates:
            nearest = max(candidates, key=lambda item: item.price)
            return nearest.level_id or f"resistance:{nearest.price}"
    else:
        candidates = [
            level
            for level in levels
            if level.kind.lower() in {"support", "swing_low", "balance_low"}
            and current.close < level.price - buffer
        ]
        if candidates:
            nearest = min(candidates, key=lambda item: item.price)
            return nearest.level_id or f"support:{nearest.price}"

    prior = bars[max(0, index - 10) : index]
    if len(prior) < 5:
        return None
    if direction is Direction.LONG:
        boundary = max(bar.high for bar in prior)
        return f"balance_high:{boundary}" if current.close > boundary + buffer else None
    boundary = min(bar.low for bar in prior)
    return f"balance_low:{boundary}" if current.close < boundary - buffer else None


def detect_displacement(
    bars: Sequence[Bar],
    index: int,
    atr: Decimal,
    levels: Sequence[StructuralLevel] = (),
    config: IRSConfig = IRSConfig(),
) -> DisplacementResult:
    ordered = completed_bars(bars)
    if index < 0:
        index += len(ordered)
    if index <= 0 or index >= len(ordered):
        return DisplacementResult(False, None, {}, ("INCOMPLETE_SETUP_BAR",), None, "")
    current = ordered[index]
    previous = ordered[index - 1]
    if atr <= ZERO or current.range <= ZERO:
        return DisplacementResult(False, None, {}, ("INVALID_BAR_DATA",), None, current.bar_id)

    if current.close > current.open:
        direction = Direction.LONG
        close_location = _ratio(current.close - current.low, current.range)
        directional_efficiency = _ratio(current.close - current.open, current.range)
    elif current.close < current.open:
        direction = Direction.SHORT
        close_location = _ratio(current.high - current.close, current.range)
        directional_efficiency = _ratio(current.open - current.close, current.range)
    else:
        return DisplacementResult(False, None, {}, ("DIRECTION_MISSING",), None, current.bar_id)

    previous_close = ordered[index - 1].close
    true_range = calculate_true_range(current, previous_close)
    body_ratio = _ratio(current.body, true_range)
    overlap_ratio = calculate_overlap_ratio(current, previous)
    relative_volume = calculate_relative_volume(ordered, index)
    atr_multiple = _ratio(true_range, atr)
    structure = _structure_reference(ordered, index, direction, atr, levels, config.tick_size)

    failed: list[str] = []
    if atr_multiple < config.min_true_range_atr_multiple:
        failed.append(RejectionReason.DISPLACEMENT_TOO_SMALL.value)
    if body_ratio < config.min_body_ratio:
        failed.append(RejectionReason.BODY_RATIO_TOO_LOW.value)
    if close_location < config.min_close_location:
        failed.append(RejectionReason.CLOSE_LOCATION_TOO_WEAK.value)
    if directional_efficiency < config.min_directional_efficiency:
        failed.append(RejectionReason.DIRECTIONAL_EFFICIENCY_TOO_LOW.value)
    if overlap_ratio > config.max_overlap_ratio:
        failed.append(RejectionReason.OVERLAP_TOO_HIGH.value)
    if relative_volume is None or relative_volume < config.min_relative_volume:
        failed.append(RejectionReason.RELATIVE_VOLUME_TOO_LOW.value)
    if structure is None:
        failed.append(RejectionReason.NO_MEANINGFUL_STRUCTURE.value)
    metrics = {
        "true_range": true_range,
        "atr_multiple": atr_multiple,
        "body_ratio": body_ratio,
        "close_location": close_location,
        "directional_efficiency": directional_efficiency,
        "overlap_ratio": overlap_ratio,
        "relative_volume": relative_volume,
        "thresholds": {
            "min_true_range_atr_multiple": config.min_true_range_atr_multiple,
            "min_body_ratio": config.min_body_ratio,
            "min_close_location": config.min_close_location,
            "min_directional_efficiency": config.min_directional_efficiency,
            "max_overlap_ratio": config.max_overlap_ratio,
            "min_relative_volume": config.min_relative_volume,
        },
    }
    return DisplacementResult(not failed, direction, metrics, tuple(failed), structure, current.bar_id)


def _zone_id(
    symbol: str,
    direction: Direction,
    zone_type: ZoneType,
    displacement_time: datetime,
    low: Decimal,
    high: Decimal,
) -> str:
    token = "|".join(
        (symbol.upper(), direction.value, zone_type.value, displacement_time.isoformat(), str(low), str(high))
    )
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def _add_rth_hours(start: datetime, hours: int) -> datetime:
    """Advance through weekday 09:30-16:00 sessions."""
    cursor = start
    remaining = timedelta(hours=max(0, hours))
    while remaining > timedelta(0):
        while cursor.weekday() >= 5:
            cursor = datetime.combine(
                cursor.date() + timedelta(days=1),
                time(9, 30),
                tzinfo=cursor.tzinfo,
            )
        session_open = datetime.combine(cursor.date(), time(9, 30), tzinfo=cursor.tzinfo)
        session_close = datetime.combine(cursor.date(), time(16, 0), tzinfo=cursor.tzinfo)
        if cursor < session_open:
            cursor = session_open
        if cursor >= session_close:
            cursor = datetime.combine(
                cursor.date() + timedelta(days=1),
                time(9, 30),
                tzinfo=cursor.tzinfo,
            )
            continue
        step = min(remaining, session_close - cursor)
        cursor += step
        remaining -= step
    return cursor


def _make_zone(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementResult,
    atr: Decimal,
    low: Decimal,
    high: Decimal,
    zone_type: ZoneType,
    source_indexes: Sequence[int],
    config: IRSConfig,
) -> tuple[InefficiencyZone | None, tuple[str, ...]]:
    width = high - low
    width_atr = _ratio(width, atr)
    reasons: list[str] = []
    if width <= ZERO:
        reasons.append(RejectionReason.ZONE_INVALID.value)
    if width_atr < config.min_zone_width_atr:
        reasons.append(RejectionReason.ZONE_TOO_NARROW.value)
    if width_atr > config.max_zone_width_atr:
        reasons.append(RejectionReason.ZONE_TOO_WIDE.value)
    if reasons or displacement.direction is None:
        return None, tuple(reasons)
    bar = bars[index]
    relative_volume = displacement.metrics.get("relative_volume") or ZERO
    zone = InefficiencyZone(
        zone_id=_zone_id(bar.symbol, displacement.direction, zone_type, bar.timestamp, low, high),
        symbol=bar.symbol,
        direction=displacement.direction,
        zone_type=zone_type,
        source_timeframe=bar.timeframe,
        created_at=bar.timestamp,
        displacement_bar_time=bar.timestamp,
        zone_low=low,
        zone_high=high,
        zone_mid=(low + high) / Decimal("2"),
        zone_width=width,
        zone_width_atr=width_atr,
        displacement_atr_multiple=_decimal(displacement.metrics["atr_multiple"]),
        body_ratio=_decimal(displacement.metrics["body_ratio"]),
        close_location=_decimal(displacement.metrics["close_location"]),
        relative_volume=_decimal(relative_volume),
        source_bar_ids=tuple(bars[item].bar_id for item in source_indexes),
        structure_reference_id=displacement.structure_reference,
        expires_at=_add_rth_hours(bar.timestamp, config.max_retrace_wait_1h_bars),
    )
    return zone, ()


def build_strict_gap_zone(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementResult,
    atr: Decimal,
    config: IRSConfig = IRSConfig(),
) -> tuple[InefficiencyZone | None, tuple[str, ...]]:
    ordered = completed_bars(bars)
    if index < 0:
        index += len(ordered)
    if index < 2 or index >= len(ordered) or displacement.direction is None:
        return None, (RejectionReason.ZONE_INVALID.value,)
    before = ordered[index - 2]
    current = ordered[index]
    if displacement.direction is Direction.LONG and before.high < current.low:
        return _make_zone(
            ordered, index, displacement, atr, before.high, current.low,
            ZoneType.STRICT_THREE_BAR_GAP, (index - 2, index - 1, index), config,
        )
    if displacement.direction is Direction.SHORT and before.low > current.high:
        return _make_zone(
            ordered, index, displacement, atr, current.high, before.low,
            ZoneType.STRICT_THREE_BAR_GAP, (index - 2, index - 1, index), config,
        )
    return None, (RejectionReason.ZONE_INVALID.value,)


def build_low_overlap_zone(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementResult,
    atr: Decimal,
    config: IRSConfig = IRSConfig(),
) -> tuple[InefficiencyZone | None, tuple[str, ...]]:
    ordered = completed_bars(bars)
    if index < 0:
        index += len(ordered)
    if index <= 0 or index >= len(ordered) or displacement.direction is None:
        return None, (RejectionReason.ZONE_INVALID.value,)
    previous = ordered[index - 1]
    current = ordered[index]
    if displacement.direction is Direction.LONG:
        low = max(previous.high, current.open)
        high = current.close - current.body * Decimal("0.25")
    else:
        low = current.close + current.body * Decimal("0.25")
        high = min(previous.low, current.open)
    return _make_zone(
        ordered, index, displacement, atr, low, high,
        ZoneType.LOW_OVERLAP_DISPLACEMENT, (index - 1, index), config,
    )


def build_inefficiency_zone(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementResult,
    atr: Decimal,
    config: IRSConfig = IRSConfig(),
) -> tuple[InefficiencyZone | None, tuple[str, ...]]:
    strict, strict_reasons = build_strict_gap_zone(bars, index, displacement, atr, config)
    if strict is not None:
        return strict, ()
    low_overlap, overlap_reasons = build_low_overlap_zone(bars, index, displacement, atr, config)
    if low_overlap is not None:
        return low_overlap, ()
    return None, tuple(dict.fromkeys((*strict_reasons, *overlap_reasons)))


def calculate_retrace_depth(zone: InefficiencyZone, bar: Bar) -> Decimal:
    if zone.direction is Direction.LONG:
        raw = (zone.zone_high - bar.low) / zone.zone_width
    else:
        raw = (bar.high - zone.zone_low) / zone.zone_width
    return _bounded(raw)


def evaluate_retracement(
    zone: InefficiencyZone,
    bars_15m: Sequence[Bar],
    displacement_volume: int,
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> RetraceResult:
    all_bars = tuple(
        bar for bar in completed_bars(bars_15m) if bar.timestamp > zone.created_at
    )
    if not all_bars:
        return RetraceResult(False, None, False, None, None, False, False, (), None, None)
    bars_per_hour = 4
    wait_bars = config.min_retrace_wait_1h_bars * bars_per_hour
    max_bars = config.max_retrace_wait_1h_bars * bars_per_hour
    bars = all_bars[wait_bars : wait_bars + max_bars]
    if not bars:
        return RetraceResult(
            False,
            None,
            False,
            None,
            None,
            False,
            False,
            (),
            None,
            None,
            {
                "bars_observed": len(all_bars),
                "eligible_bars_observed": 0,
                "waiting_for_minimum_retrace_delay": True,
            },
        )
    invalidation_buffer = max(config.tick_size * 2, atr_15m * Decimal("0.05"))
    adverse_closes = 0
    touch: Bar | None = None
    deepest: Decimal | None = None
    sweep_extreme: Decimal | None = None
    pullback: list[Bar] = []
    invalidated = False
    reasons: list[str] = []

    for bar in bars:
        adverse = (
            bar.close < zone.zone_low - invalidation_buffer
            if zone.direction is Direction.LONG
            else bar.close > zone.zone_high + invalidation_buffer
        )
        adverse_closes = adverse_closes + 1 if adverse else 0
        if adverse_closes >= 2:
            invalidated = True
            reasons.append(RejectionReason.ZONE_ACCEPTED_AGAINST_TRADE.value)
            break
        intersects = bar.low <= zone.zone_high and bar.high >= zone.zone_low
        if intersects:
            touch = touch or bar
            pullback.append(bar)
            depth = calculate_retrace_depth(zone, bar)
            if deepest is None or depth > deepest:
                deepest = depth
                sweep_extreme = bar.low if zone.direction is Direction.LONG else bar.high
        elif touch is not None:
            pullback.append(bar)

    expired = len(all_bars) > wait_bars + max_bars and touch is None
    if expired:
        reasons.append(RejectionReason.ZONE_EXPIRED.value)
    if deepest is not None and deepest < config.min_retrace_depth:
        reasons.append(RejectionReason.RETRACE_TOO_SHALLOW.value)
    if deepest is not None and deepest > config.max_retrace_depth:
        invalidated = True
        reasons.append(RejectionReason.RETRACE_TOO_DEEP.value)

    median_volume = Decimal(str(statistics.median([bar.volume for bar in pullback]))) if pullback else ZERO
    volume_ratio = _ratio(median_volume, Decimal(max(displacement_volume, 1))) if pullback else None
    prior_volumes = [bar.volume for bar in bars[: max(0, bars.index(touch))]] if touch in bars else []
    baseline = Decimal(str(statistics.median(prior_volumes[-20:]))) if prior_volumes else ZERO
    relative_volume = _ratio(median_volume, baseline) if baseline > ZERO else None
    if volume_ratio is not None and volume_ratio > config.max_pullback_volume_ratio:
        reasons.append(RejectionReason.PULLBACK_VOLUME_TOO_HIGH.value)
    if relative_volume is not None and relative_volume > config.max_pullback_relative_volume:
        reasons.append(RejectionReason.PULLBACK_VOLUME_TOO_HIGH.value)

    volume_ok = (
        (volume_ratio is None or volume_ratio <= config.max_pullback_volume_ratio)
        and (
            relative_volume is None
            or relative_volume <= config.max_pullback_relative_volume
        )
    )
    detected = (
        touch is not None
        and deepest is not None
        and deepest >= config.min_retrace_depth
        and volume_ok
        and not invalidated
        and not expired
    )
    preferred = bool(
        deepest is not None
        and config.preferred_retrace_min <= deepest <= config.preferred_retrace_max
    )
    return RetraceResult(
        detected=detected,
        depth=deepest,
        preferred=preferred,
        pullback_volume_ratio=volume_ratio,
        pullback_relative_volume=relative_volume,
        invalidated=invalidated,
        expired=expired,
        reasons=tuple(dict.fromkeys(reasons)),
        first_touch_time=touch.timestamp if touch else None,
        sweep_extreme=sweep_extreme,
        metrics={
            "bars_observed": len(all_bars),
            "eligible_bars_observed": len(bars),
            "minimum_wait_bars": wait_bars,
            "maximum_wait_bars": max_bars,
            "pullback_bars": len(pullback),
            "invalidation_buffer": invalidation_buffer,
        },
    )


def _close_location(bar: Bar, direction: Direction) -> Decimal:
    if direction is Direction.LONG:
        return _ratio(bar.close - bar.low, bar.range)
    return _ratio(bar.high - bar.close, bar.range)


def detect_sweep_and_reclaim(
    zone: InefficiencyZone,
    bars: Sequence[Bar],
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> ConfirmationEvent | None:
    boundary = zone.zone_mid
    for bar in reversed(completed_bars(bars)):
        if bar.timestamp <= zone.created_at or bar.range <= ZERO:
            continue
        if zone.direction is Direction.LONG:
            penetration = boundary - bar.low
            wick = min(bar.open, bar.close) - bar.low
            reclaimed = bar.low < boundary and bar.close > boundary
        else:
            penetration = bar.high - boundary
            wick = bar.high - max(bar.open, bar.close)
            reclaimed = bar.high > boundary and bar.close < boundary
        penetration_atr = _ratio(penetration, atr_15m)
        wick_ratio = _ratio(wick, bar.range)
        location = _close_location(bar, zone.direction)
        if (
            reclaimed
            and config.min_sweep_penetration_atr
            <= penetration_atr
            <= config.max_sweep_penetration_atr
            and wick_ratio >= config.min_rejection_wick_ratio
            and location >= config.min_reclaim_close_location
        ):
            quality = _bounded(
                Decimal("0.40") * location
                + Decimal("0.30") * min(Decimal("1"), wick_ratio / config.min_rejection_wick_ratio)
                + Decimal("0.30")
            )
            return ConfirmationEvent(
                ConfirmationType.SWEEP_AND_RECLAIM,
                bar.timestamp,
                bar.bar_id,
                bar.high,
                bar.low,
                boundary,
                quality,
                {
                    "penetration_atr": penetration_atr,
                    "wick_ratio": wick_ratio,
                    "close_location": location,
                },
            )
    return None


def detect_two_bar_reclaim(
    zone: InefficiencyZone,
    bars: Sequence[Bar],
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> ConfirmationEvent | None:
    ordered = completed_bars(bars)
    for index in range(len(ordered) - 1, 0, -1):
        first, second = ordered[index - 1], ordered[index]
        if first.timestamp <= zone.created_at:
            continue
        first_tests = first.low <= zone.zone_high if zone.direction is Direction.LONG else first.high >= zone.zone_low
        if zone.direction is Direction.LONG:
            reclaimed = second.close > max(first.high, zone.zone_mid)
            extension = second.close - zone.zone_high
        else:
            reclaimed = second.close < min(first.low, zone.zone_mid)
            extension = zone.zone_low - second.close
        chase = _ratio(max(ZERO, extension), atr_15m) > config.max_entry_extension_atr
        combined_volume_ok = second.volume >= int(first.volume * 0.80)
        if first_tests and reclaimed and not chase and combined_volume_ok:
            return ConfirmationEvent(
                ConfirmationType.TWO_BAR_RECLAIM,
                second.timestamp,
                second.bar_id,
                second.high,
                second.low,
                zone.zone_mid,
                Decimal("0.80"),
                {
                    "first_bar_id": first.bar_id,
                    "combined_volume": first.volume + second.volume,
                    "extension_atr": _ratio(max(ZERO, extension), atr_15m),
                },
            )
    return None


def detect_micro_break_of_structure(
    zone: InefficiencyZone,
    bars: Sequence[Bar],
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> ConfirmationEvent | None:
    ordered = completed_bars(bars)
    if len(ordered) < 4:
        return None
    trigger = ordered[-1]
    buffer = max(config.tick_size * 2, atr_15m * Decimal("0.02"))
    # A pivot at i is historically available only after bar i+1 closes.
    for index in range(len(ordered) - 3, 0, -1):
        left, pivot, right = ordered[index - 1], ordered[index], ordered[index + 1]
        if pivot.timestamp <= zone.created_at:
            continue
        if zone.direction is Direction.LONG:
            confirmed_pivot = pivot.high > left.high and pivot.high >= right.high
            broken = trigger.close > pivot.high + buffer
            boundary = pivot.high
        else:
            confirmed_pivot = pivot.low < left.low and pivot.low <= right.low
            broken = trigger.close < pivot.low - buffer
            boundary = pivot.low
        if confirmed_pivot and broken and trigger.timestamp > right.timestamp:
            return ConfirmationEvent(
                ConfirmationType.MICRO_BREAK_OF_STRUCTURE,
                trigger.timestamp,
                trigger.bar_id,
                trigger.high,
                trigger.low,
                boundary,
                Decimal("0.85"),
                {"pivot_bar_id": pivot.bar_id, "pivot_available_at": right.timestamp, "buffer": buffer},
            )
    return None


def choose_confirmation(
    zone: InefficiencyZone,
    bars: Sequence[Bar],
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> ConfirmationEvent | None:
    return (
        detect_sweep_and_reclaim(zone, bars, atr_15m, config)
        or detect_micro_break_of_structure(zone, bars, atr_15m, config)
        or detect_two_bar_reclaim(zone, bars, atr_15m, config)
    )


def _round_tick(value: Decimal, tick: Decimal, rounding: str) -> Decimal:
    return (value / tick).to_integral_value(rounding=rounding) * tick


def calculate_entry_trigger(
    direction: Direction,
    confirmation: ConfirmationEvent,
    atr_15m: Decimal,
    config: IRSConfig = IRSConfig(),
) -> tuple[Decimal, Decimal]:
    buffer = max(config.tick_size * 2, atr_15m * config.entry_buffer_atr)
    slippage = max(config.tick_size * 2, config.expected_slippage_per_share)
    if direction is Direction.LONG:
        stop = _round_tick(confirmation.high + buffer, config.tick_size, ROUND_CEILING)
        limit = _round_tick(stop + slippage, config.tick_size, ROUND_CEILING)
    else:
        stop = _round_tick(confirmation.low - buffer, config.tick_size, ROUND_FLOOR)
        limit = _round_tick(stop - slippage, config.tick_size, ROUND_FLOOR)
    return stop, limit


def calculate_technical_stop(
    direction: Direction,
    entry: Decimal,
    zone: InefficiencyZone,
    confirmation: ConfirmationEvent,
    atr_15m: Decimal,
    recent_swing: Decimal | None,
    config: IRSConfig = IRSConfig(),
) -> tuple[Decimal | None, str | None]:
    references = [zone.zone_low, confirmation.low] if direction is Direction.LONG else [zone.zone_high, confirmation.high]
    if recent_swing is not None:
        references.append(recent_swing)
    buffer = max(config.tick_size * 2, atr_15m * config.stop_buffer_atr)
    if direction is Direction.LONG:
        stop = _round_tick(min(references) - buffer, config.tick_size, ROUND_FLOOR)
        distance = entry - stop
    else:
        stop = _round_tick(max(references) + buffer, config.tick_size, ROUND_CEILING)
        distance = stop - entry
    if distance <= ZERO:
        return None, RejectionReason.INVALID_STOP.value
    distance_atr = _ratio(distance, atr_15m)
    if distance_atr < config.min_stop_distance_atr:
        return None, RejectionReason.STOP_TOO_TIGHT.value
    if distance_atr > config.max_stop_distance_atr:
        return None, RejectionReason.STOP_TOO_WIDE.value
    return stop, None


def find_structural_target(
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    levels: Sequence[StructuralLevel],
    config: IRSConfig = IRSConfig(),
) -> Decimal:
    if direction is Direction.LONG:
        obstacles = sorted(level.price for level in levels if level.price > entry)
        fallback = entry + abs(entry - stop) * config.target_r
        raw = obstacles[0] if obstacles else fallback
        return _round_tick(raw, config.tick_size, ROUND_FLOOR)
    obstacles = sorted((level.price for level in levels if level.price < entry), reverse=True)
    fallback = entry - abs(entry - stop) * config.target_r
    raw = obstacles[0] if obstacles else fallback
    return _round_tick(raw, config.tick_size, ROUND_CEILING)


def calculate_structural_r(
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    costs: Decimal = ZERO,
) -> Decimal | None:
    price_risk = abs(entry - stop)
    risk = price_risk + costs
    reward = (target - entry if direction is Direction.LONG else entry - target) - costs
    if risk <= ZERO or reward <= ZERO:
        return None
    return reward / risk


def size_position(
    account: AccountState,
    entry: Decimal,
    stop: Decimal,
    config: IRSConfig = IRSConfig(),
) -> tuple[int, Decimal, Decimal]:
    risk_cash = min(account.equity * config.risk_percent_per_trade, config.max_fixed_risk_cash)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= ZERO or entry <= ZERO:
        return 0, risk_cash, ZERO
    raw = int(risk_cash // risk_per_share)
    caps = [
        raw,
        config.max_shares,
        int(config.max_position_value // entry),
        int(account.buying_power // entry),
    ]
    quantity = max(0, min(caps))
    return quantity, risk_cash, entry * quantity


def score_candidate(
    displacement: DisplacementResult,
    zone: InefficiencyZone,
    retrace: RetraceResult,
    confirmation: ConfirmationEvent | None,
    regime_aligned: bool,
    structural_r: Decimal | None,
    config: IRSConfig = IRSConfig(),
) -> ScoreBreakdown:
    metrics = displacement.metrics
    displacement_quality = statistics.fmean(
        [
            float(_bounded(_decimal(metrics.get("atr_multiple", 0)) / config.min_true_range_atr_multiple)),
            float(_bounded(_decimal(metrics.get("body_ratio", 0)) / config.min_body_ratio)),
            float(_bounded(_decimal(metrics.get("close_location", 0)) / config.min_close_location)),
            float(
                _bounded(
                    _decimal(metrics.get("directional_efficiency", 0))
                    / config.min_directional_efficiency
                )
            ),
        ]
    )
    zone_center = (config.min_zone_width_atr + config.max_zone_width_atr) / Decimal("2")
    zone_half = (config.max_zone_width_atr - config.min_zone_width_atr) / Decimal("2")
    zone_quality = Decimal("1") - _bounded(abs(zone.zone_width_atr - zone_center) / max(zone_half, Decimal("0.0001")))
    if retrace.preferred:
        retrace_quality = Decimal("1")
    elif retrace.depth is not None:
        retrace_quality = Decimal("0.65")
    else:
        retrace_quality = ZERO
    relative_volume = _decimal(metrics.get("relative_volume") or 0)
    return ScoreBreakdown(
        structure=Decimal("15") if displacement.structure_reference else ZERO,
        displacement=(Decimal("15") * Decimal(str(displacement_quality))).quantize(Decimal("0.01")),
        zone=(Decimal("10") * zone_quality).quantize(Decimal("0.01")),
        volume_order_flow=(
            Decimal("15") * _bounded(relative_volume / config.min_relative_volume)
        ).quantize(Decimal("0.01")),
        retrace=(Decimal("10") * retrace_quality).quantize(Decimal("0.01")),
        confirmation=(
            Decimal("15") * (confirmation.score if confirmation else ZERO)
        ).quantize(Decimal("0.01")),
        regime=Decimal("10") if regime_aligned else ZERO,
        target_space=(
            Decimal("10")
            * _bounded((structural_r or ZERO) / config.preferred_minimum_r)
        ).quantize(Decimal("0.01")),
    )


def evaluate_hard_gates(
    context: StrategyContext,
    direction: Direction,
    score: Decimal,
    structural_r: Decimal | None,
    config: IRSConfig = IRSConfig(),
) -> tuple[str, ...]:
    reasons: list[str] = []
    daily = completed_bars(context.daily_bars, context.as_of)
    if len(daily) < 20:
        reasons.append(RejectionReason.INSUFFICIENT_DAILY_HISTORY.value)
    if context.quote is None:
        reasons.append(RejectionReason.MISSING_QUOTE.value)
    else:
        age = (context.as_of - context.quote.timestamp).total_seconds()
        if age < 0 or age > config.max_quote_age_seconds:
            reasons.append(RejectionReason.STALE_QUOTE.value)
        spread = context.quote.spread_percent
        if spread is None:
            reasons.append(RejectionReason.MISSING_QUOTE.value)
        elif spread > config.max_spread_percent:
            reasons.append(RejectionReason.SPREAD_TOO_WIDE.value)
    if context.news_status is RiskStatus.UNKNOWN:
        reasons.append(RejectionReason.NEWS_STATUS_UNAVAILABLE.value)
    elif context.news_status is RiskStatus.BLOCKED:
        reasons.append(RejectionReason.NEWS_RISK.value)
    if context.earnings_status is RiskStatus.UNKNOWN:
        reasons.append(RejectionReason.NEWS_STATUS_UNAVAILABLE.value)
    elif context.earnings_status is RiskStatus.BLOCKED:
        reasons.append(RejectionReason.EARNINGS_BLOCK.value)
    if context.corporate_action_status is not RiskStatus.CLEAR:
        reasons.append(RejectionReason.INVALID_BAR_DATA.value)
    if context.halt_status is not RiskStatus.CLEAR:
        reasons.append(RejectionReason.LIQUIDITY_TOO_LOW.value)
    if structural_r is None or structural_r < config.hard_minimum_r:
        reasons.append(RejectionReason.INSUFFICIENT_TARGET_SPACE.value)
    if score < config.minimum_order_score:
        reasons.append(RejectionReason.SCORE_TOO_LOW.value)
    if not context.session_entry_allowed:
        reasons.append(RejectionReason.SESSION_CUTOFF.value)
    if context.sector_regime in {None, MarketRegime.UNKNOWN}:
        reasons.append(RejectionReason.SECTOR_REGIME_OPPOSED.value)
    account = context.account_state
    if account is None:
        reasons.append(RejectionReason.BUYING_POWER.value)
    else:
        if context.symbol.upper() in {item.upper() for item in account.existing_symbols}:
            reasons.append(RejectionReason.POSITION_EXISTS.value)
        if context.symbol.upper() in {item.upper() for item in account.pending_symbols}:
            reasons.append(RejectionReason.PENDING_ORDER_EXISTS.value)
        if account.open_positions >= config.max_positions:
            reasons.append(RejectionReason.MAX_POSITIONS.value)
        if account.trades_today >= config.max_trades_per_day:
            reasons.append(RejectionReason.DAILY_TRADE_LIMIT.value)
        if account.daily_loss >= account.equity * config.max_daily_loss_percent:
            reasons.append(RejectionReason.DAILY_LOSS_LOCKOUT.value)
        if not account.protective_stop_available:
            reasons.append(RejectionReason.PROTECTIVE_STOP_UNAVAILABLE.value)
        if not account.broker_connected:
            reasons.append(RejectionReason.BROKER_DISCONNECTED.value)
        if direction is Direction.SHORT and not account.short_available:
            reasons.append(RejectionReason.BUYING_POWER.value)
    return tuple(dict.fromkeys(reasons))


def build_signal_id(
    symbol: str,
    profile: StrategyProfile,
    zone_id: str,
    confirmation_time: datetime,
    direction: Direction,
) -> str:
    token = "|".join(
        (
            symbol.upper(),
            STRATEGY_CODE,
            profile.value,
            zone_id,
            confirmation_time.isoformat(),
            direction.value,
        )
    )
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:28]


def grade_for_score(score: Decimal) -> str:
    if score >= Decimal("90"):
        return "A+"
    if score >= Decimal("80"):
        return "A"
    if score >= Decimal("72"):
        return "B"
    return "C"


def build_explanation(
    context: StrategyContext,
    zone: InefficiencyZone,
    retrace: RetraceResult,
    confirmation: ConfirmationEvent | None,
    score: Decimal,
    order_plan: OrderPlan | None,
    hard_rejections: Sequence[str],
) -> str:
    parts = [f"{context.symbol} | {zone.direction.value} | Score {score:.0f}"]
    parts.append(
        f"{zone.direction.value} {zone.source_timeframe} displacement "
        f"{zone.displacement_atr_multiple:.2f} robust ATR, body {zone.body_ratio:.0%}, "
        f"RVOL {zone.relative_volume:.2f}."
    )
    if retrace.depth is not None:
        parts.append(
            f"Price retraced {retrace.depth:.0%} into a {zone.zone_type.value.lower()} zone."
        )
    if confirmation is not None:
        parts.append(f"{confirmation.confirmation_type.value} confirmed at {confirmation.timestamp.isoformat()}.")
    if order_plan is not None:
        parts.append(
            f"Entry {order_plan.entry_stop}, stop {order_plan.stop}, target {order_plan.target}, "
            f"structural R {order_plan.structural_r:.2f}."
        )
    parts.append("PAPER.")
    if hard_rejections:
        parts.append(f"No order: {', '.join(hard_rejections)}.")
    return " ".join(parts)


def _regime_aligned(context: StrategyContext, direction: Direction) -> bool:
    if context.market_regime not in {MarketRegime.TREND_LOW_VOL, MarketRegime.TREND_HIGH_VOL}:
        return False
    expected = "LONG" if direction is Direction.LONG else "SHORT"
    if context.market_trend_direction != expected:
        return False
    if (
        context.sector_regime in {MarketRegime.TREND_LOW_VOL, MarketRegime.TREND_HIGH_VOL}
        and context.sector_trend_direction not in {None, expected}
    ):
        return False
    if context.relative_strength_20d is not None:
        return context.relative_strength_20d >= ZERO if direction is Direction.LONG else context.relative_strength_20d <= ZERO
    return True


def evaluate_strategy(
    context: StrategyContext,
    config: IRSConfig = IRSConfig(),
) -> tuple[StrategyCandidate, ...]:
    """Evaluate all historically available displacement zones deterministically."""
    config.validate()
    hourly = completed_bars(context.hourly_bars, context.as_of)
    bars_15m = completed_bars(context.bars_15m, context.as_of)
    if len(hourly) < config.robust_atr_min_values + 2 or len(bars_15m) < config.robust_atr_min_values:
        return ()
    atr_15m = calculate_robust_atr(bars_15m, config)
    if atr_15m is None:
        return ()

    candidates: list[StrategyCandidate] = []
    search_start = max(config.robust_atr_min_values + 1, len(hourly) - config.max_retrace_wait_1h_bars - 3)
    for index in range(search_start, len(hourly)):
        atr_hourly = calculate_robust_atr(hourly, config, end_index=index)
        if atr_hourly is None:
            continue
        displacement = detect_displacement(hourly, index, atr_hourly, context.levels, config)
        if not displacement.passed:
            continue
        zone, zone_reasons = build_inefficiency_zone(hourly, index, displacement, atr_hourly, config)
        if zone is None:
            continue
        retrace = evaluate_retracement(zone, bars_15m, hourly[index].volume, atr_15m, config)
        hourly_acceptance = any(
            (
                bar.close < zone.zone_low
                if zone.direction is Direction.LONG
                else bar.close > zone.zone_high
            )
            for bar in hourly[index + 1 :]
        )
        if hourly_acceptance and not retrace.invalidated:
            retrace = RetraceResult(
                detected=False,
                depth=retrace.depth,
                preferred=False,
                pullback_volume_ratio=retrace.pullback_volume_ratio,
                pullback_relative_volume=retrace.pullback_relative_volume,
                invalidated=True,
                expired=retrace.expired,
                reasons=tuple(
                    dict.fromkeys(
                        (
                            *retrace.reasons,
                            RejectionReason.ZONE_ACCEPTED_AGAINST_TRADE.value,
                        )
                    )
                ),
                first_touch_time=retrace.first_touch_time,
                sweep_extreme=retrace.sweep_extreme,
                metrics={**retrace.metrics, "hourly_close_invalidation": True},
            )
        post_touch = [
            bar
            for bar in bars_15m
            if retrace.first_touch_time is not None and bar.timestamp >= retrace.first_touch_time
        ]
        confirmation_window = post_touch[: config.max_confirmation_15m_bars]
        confirmation = (
            choose_confirmation(zone, confirmation_window, atr_15m, config)
            if retrace.detected and not retrace.invalidated
            else None
        )
        confirmation_expired = (
            retrace.detected
            and confirmation is None
            and len(post_touch) > config.max_confirmation_15m_bars
        )
        order_plan: OrderPlan | None = None
        structural_r: Decimal | None = None
        plan_rejections: list[str] = list(zone_reasons)
        if retrace.invalidated:
            plan_rejections.extend(retrace.reasons)
        elif confirmation_expired:
            plan_rejections.append(RejectionReason.CONFIRMATION_EXPIRED.value)
        elif confirmation is not None:
            entry, entry_limit = calculate_entry_trigger(zone.direction, confirmation, atr_15m, config)
            stop, stop_reason = calculate_technical_stop(
                zone.direction,
                entry,
                zone,
                confirmation,
                atr_15m,
                retrace.sweep_extreme,
                config,
            )
            if stop is None:
                plan_rejections.append(stop_reason or RejectionReason.INVALID_STOP.value)
            else:
                target = find_structural_target(zone.direction, entry, stop, context.levels, config)
                estimated_costs = config.commission_per_share + config.expected_slippage_per_share
                structural_r = calculate_structural_r(
                    zone.direction, entry, stop, target, estimated_costs
                )
                account = context.account_state or AccountState(ZERO, ZERO)
                quantity, risk_cash, buying_power = size_position(account, entry_limit, stop, config)
                if context.account_state is not None and quantity <= 0:
                    plan_rejections.append(RejectionReason.BUYING_POWER.value)
                reward = (
                    target - entry if zone.direction is Direction.LONG else entry - target
                ) - estimated_costs
                order_plan = OrderPlan(
                    entry_stop=entry,
                    entry_limit=entry_limit,
                    stop=stop,
                    target=target,
                    risk_per_share=abs(entry_limit - stop) + estimated_costs,
                    reward_per_share=max(ZERO, reward),
                    structural_r=structural_r or ZERO,
                    estimated_costs=estimated_costs,
                    quantity=quantity,
                    risk_cash=risk_cash,
                    required_buying_power=buying_power,
                )

        regime_aligned = _regime_aligned(context, zone.direction)
        breakdown = score_candidate(
            displacement, zone, retrace, confirmation, regime_aligned, structural_r, config
        )
        hard = list(
            evaluate_hard_gates(context, zone.direction, breakdown.total, structural_r, config)
        )
        hard.extend(plan_rejections)
        if not regime_aligned:
            hard.append(RejectionReason.MARKET_REGIME_OPPOSED.value)
        hard = list(dict.fromkeys(hard))
        if retrace.invalidated:
            state = SetupState.INVALIDATED
        elif retrace.expired or confirmation_expired:
            state = SetupState.EXPIRED
        elif confirmation is None and retrace.detected:
            state = SetupState.WAITING_FOR_CONFIRMATION
        elif confirmation is None:
            state = SetupState.WAITING_FOR_RETRACE
        elif hard:
            state = (
                SetupState.REJECTED_BY_NEWS
                if any(reason in hard for reason in (RejectionReason.NEWS_RISK.value, RejectionReason.NEWS_STATUS_UNAVAILABLE.value, RejectionReason.EARNINGS_BLOCK.value))
                else SetupState.REJECTED_BY_TARGET_SPACE
                if RejectionReason.INSUFFICIENT_TARGET_SPACE.value in hard
                else SetupState.REJECTED_BY_RISK
            )
        else:
            state = SetupState.ENTRY_ARMED
        confirmation_time = confirmation.timestamp if confirmation else zone.created_at
        signal_id = build_signal_id(
            context.symbol, config.profile, zone.zone_id, confirmation_time, zone.direction
        )
        explanation = build_explanation(
            context, zone, retrace, confirmation, breakdown.total, order_plan, hard
        )
        candidates.append(
            StrategyCandidate(
                signal_id=signal_id,
                symbol=context.symbol,
                strategy=STRATEGY_CODE,
                profile=config.profile,
                direction=zone.direction,
                state=state,
                score=breakdown.total,
                score_breakdown=breakdown,
                zone=zone,
                displacement_metrics=displacement.metrics,
                retrace_metrics={
                    "depth": retrace.depth,
                    "preferred": retrace.preferred,
                    "pullback_volume_ratio": retrace.pullback_volume_ratio,
                    "pullback_relative_volume": retrace.pullback_relative_volume,
                    "first_touch_time": retrace.first_touch_time,
                    **retrace.metrics,
                },
                confirmation=confirmation,
                order_plan=order_plan,
                expires_at=(
                    confirmation.timestamp + timedelta(minutes=15 * config.entry_expiration_15m_bars)
                    if confirmation
                    else zone.expires_at
                ),
                hard_rejections=tuple(hard),
                soft_warnings=(
                    ("RETRACE_OUTSIDE_PREFERRED_BAND",) if retrace.detected and not retrace.preferred else ()
                ),
                diagnostics={
                    "as_of": context.as_of,
                    "hourly_atr": atr_hourly,
                    "atr_15m": atr_15m,
                    "grade": grade_for_score(breakdown.total),
                    "quote_age_seconds": (
                        (context.as_of - context.quote.timestamp).total_seconds()
                        if context.quote
                        else None
                    ),
                    "spread_percent": (
                        context.quote.spread_percent if context.quote else None
                    ),
                    "news_status": context.news_status,
                    "earnings_status": context.earnings_status,
                    "market_regime": context.market_regime,
                    "market_trend_direction": context.market_trend_direction,
                    "sector_regime": context.sector_regime,
                    "sector_trend_direction": context.sector_trend_direction,
                    "relative_strength_20d": context.relative_strength_20d,
                },
                explanation=explanation,
            )
        )
    unique = {candidate.signal_id: candidate for candidate in candidates}
    return tuple(sorted(unique.values(), key=lambda item: (item.score, item.zone.created_at), reverse=True))
