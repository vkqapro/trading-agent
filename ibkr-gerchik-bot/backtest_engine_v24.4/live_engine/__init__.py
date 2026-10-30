"""Canonical read-only closed-daily setup and live trigger preview engine."""
from .contracts import (Bar, CurrentSessionState, DailySetup, LivePreview, LiveStrategyResult,
    LiveTradePlan, SessionFreshness, SessionFreshnessPolicy, SessionFreshnessStatus,
    SetupCandidate, SetupStatus, SWING_FRESHNESS_POLICY, TriggerEvaluation, TriggerResult)
from .engine import (CanonicalLiveEngine, detect_daily_setups, detect_historical_setups,
    evaluate_entry_day, evaluate_live_setup, preview_current_session)
from .adapter import PersistedSessionAdapter, load_closed_daily_frame
__all__=["Bar","CurrentSessionState","DailySetup","LivePreview","LiveStrategyResult","LiveTradePlan","SessionFreshness","SessionFreshnessPolicy","SessionFreshnessStatus","SetupCandidate","SetupStatus","SWING_FRESHNESS_POLICY","TriggerEvaluation","TriggerResult","CanonicalLiveEngine","detect_daily_setups","detect_historical_setups","evaluate_entry_day","evaluate_live_setup","preview_current_session","PersistedSessionAdapter","load_closed_daily_frame"]
