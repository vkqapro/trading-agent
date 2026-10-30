from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config" / "hermes" / "trading-orchestrator"


def test_orchestrator_profile_is_read_only_and_a2a_scoped():
    config = (SOURCE / "config.yaml").read_text()
    soul = (SOURCE / "SOUL.md").read_text()

    assert "- a2a" in config
    assert "  telegram:\n    - hermes-telegram\n    - a2a" in config
    assert "trading-ops-supervisor:" in config
    assert "http://127.0.0.1:9900/trading-ops-supervisor" in config
    assert "http://127.0.0.1:9900/daily-trader" in config
    assert "http://127.0.0.1:9900/pre-trade-reviewer" in config
    assert "create*approval" in config
    assert "execution_intent" in config
    assert "never submit broker orders" in soul
    assert "Never replace an exact scan ID with the latest scan" in soul


def test_orchestrator_telegram_toolset_preserves_telegram_bundle_and_adds_a2a():
    config = (SOURCE / "config.yaml").read_text()

    telegram_block = config.split("  telegram:\n", 1)[1].split("approvals:\n", 1)[0]
    assert "- hermes-telegram" in telegram_block
    assert "- a2a" in telegram_block


def test_orchestrator_skill_requires_exact_scan_reviewer_handoff():
    skill = (SOURCE / "skills" / "trading-readiness-orchestrator" / "SKILL.md").read_text()

    assert "trading-ops-supervisor" in skill
    assert "daily-trader" in skill
    assert "pre-trade-reviewer" in skill
    assert '"review_scope":"all_active"' in skill
    assert '"include_terminal":true' in skill
    assert "Reviewer `scan_id` exactly equals Daily Trader `scan_id`" in skill
    assert "Do not deduplicate same-symbol tiers" in skill
    assert "Do not issue a second scan" in skill


def test_orchestrator_skill_has_deferred_a2a_invocation_contract():
    skill = (SOURCE / "skills" / "trading-readiness-orchestrator" / "SKILL.md").read_text()

    assert "tool_search" in skill
    assert 'tool_describe` with `{"names":["a2a_call"]}`' in skill
    assert '"name":"a2a_call"' in skill
    assert '"agent":"trading-ops-supervisor"' in skill
    assert "exactly one local entry" in skill
    assert "Never invoke `trading-ops-supervisor`" in skill
    assert "Wait for that response before issuing the next specialist call" in skill
