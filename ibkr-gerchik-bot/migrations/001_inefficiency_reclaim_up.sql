CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('001_inefficiency_reclaim');

CREATE TABLE IF NOT EXISTS scanner_runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    git_commit TEXT,
    symbols_scanned INTEGER NOT NULL DEFAULT 0,
    candidates INTEGER NOT NULL DEFAULT 0,
    rejections INTEGER NOT NULL DEFAULT 0,
    diagnostics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS inefficiency_zones (
    zone_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    zone_type TEXT NOT NULL,
    source_timeframe TEXT NOT NULL,
    created_at TEXT NOT NULL,
    displacement_bar_time TEXT NOT NULL,
    zone_low TEXT NOT NULL,
    zone_high TEXT NOT NULL,
    zone_mid TEXT NOT NULL,
    zone_width TEXT NOT NULL,
    zone_width_atr TEXT NOT NULL,
    displacement_atr_multiple TEXT NOT NULL,
    body_ratio TEXT NOT NULL,
    close_location TEXT NOT NULL,
    relative_volume TEXT NOT NULL,
    source_bar_ids_json TEXT NOT NULL,
    structure_reference_id TEXT,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_irs_zones_symbol_status
    ON inefficiency_zones(symbol, status, created_at);

CREATE TABLE IF NOT EXISTS strategy_setups (
    setup_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    profile TEXT NOT NULL,
    direction TEXT NOT NULL,
    zone_id TEXT NOT NULL REFERENCES inefficiency_zones(zone_id),
    state TEXT NOT NULL,
    score TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    strategy_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_irs_setups_state_expiry
    ON strategy_setups(state, expires_at);
CREATE INDEX IF NOT EXISTS idx_irs_setups_symbol
    ON strategy_setups(symbol, updated_at);

CREATE TABLE IF NOT EXISTS strategy_state_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id TEXT NOT NULL REFERENCES strategy_setups(setup_id),
    previous_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    exchange_timestamp TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_bar_id TEXT,
    strategy_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    UNIQUE(setup_id, previous_state, new_state, exchange_timestamp, run_id)
);

CREATE TABLE IF NOT EXISTS strategy_signals (
    signal_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL REFERENCES strategy_setups(setup_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    profile TEXT NOT NULL,
    direction TEXT NOT NULL,
    zone_id TEXT NOT NULL REFERENCES inefficiency_zones(zone_id),
    confirmation_time TEXT,
    state TEXT NOT NULL,
    score TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_rejections (
    rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES strategy_signals(signal_id),
    reason TEXT NOT NULL,
    state TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    UNIQUE(signal_id, reason)
);

CREATE INDEX IF NOT EXISTS idx_irs_rejections_reason
    ON strategy_rejections(reason, created_at);

CREATE TABLE IF NOT EXISTS order_plans (
    signal_id TEXT PRIMARY KEY REFERENCES strategy_signals(signal_id),
    order_ref TEXT NOT NULL UNIQUE,
    entry_stop TEXT NOT NULL,
    entry_limit TEXT NOT NULL,
    stop_price TEXT NOT NULL,
    target_price TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    risk_cash TEXT NOT NULL,
    structural_r TEXT NOT NULL,
    estimated_costs TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES strategy_signals(signal_id),
    order_ref TEXT NOT NULL UNIQUE,
    broker_order_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT NOT NULL,
    updated_at TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref TEXT NOT NULL REFERENCES orders(order_ref),
    execution_id TEXT NOT NULL UNIQUE,
    quantity INTEGER NOT NULL,
    price TEXT NOT NULL,
    filled_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_irs_risk_signal_time
    ON risk_snapshots(signal_id, captured_at);

CREATE TABLE IF NOT EXISTS news_checks (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    news_status TEXT NOT NULL,
    earnings_status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_irs_news_signal_time
    ON news_checks(signal_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_irs_news_symbol_time
    ON news_checks(symbol, checked_at);
