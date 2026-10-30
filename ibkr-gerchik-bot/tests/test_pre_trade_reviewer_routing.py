from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "config" / "hermes" / "pre-trade-reviewer" / "skills"


def test_candidate_skill_declares_unambiguous_natural_language_routing():
    text = (SKILLS / "pre-trade-review-candidate" / "SKILL.md").read_text()

    assert "`Review latest scan` -> latest batch review with no symbol filter." in text
    assert "`Review all candidates` -> latest batch review with no symbol filter." in text
    assert "`Review SYMBOL` -> latest batch review filtered by that symbol" in text
    assert "`Review candidate <full-reference>` -> exact historical candidate review." in text
    assert "Do not ask for an ordinal merely because a symbol has multiple signals." in text.replace("\n", " ")
    assert "latest --symbol KDP --format report" in text


def test_candidate_skill_declares_exact_scan_batch_routing():
    text = (SKILLS / "pre-trade-review-candidate" / "SKILL.md").read_text()

    assert "scan_id=<UUID>" in text
    assert "review_scope=all_active" in text
    assert "include_terminal=true" in text
    assert "read_only=true" in text
    assert "pre_trade_reviewer_api.py') scan" in text
    assert "Never use latest-scan lookup for scan-scoped" in text


def test_legacy_symbol_skill_cannot_claim_symbol_only_routing():
    text = (SKILLS / "pre-trade-review" / "SKILL.md").read_text()

    assert "do not use for natural-language latest, all-candidate, or symbol-only requests" in text
    assert "never resolve a symbol-only request through this skill's" in text

