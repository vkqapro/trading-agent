# IRS Golden Fixtures

Each scenario contains:

- `input_bars`: the minimal bars relevant to the behavior;
- `config`: explicit overrides from `IRSConfig`;
- `expected`: the deterministic strategy or replay result.

Intraday timestamps are bar-close availability times in
`America/New_York`. These fixtures are intentionally small and complement the
full synthetic-history unit tests.

