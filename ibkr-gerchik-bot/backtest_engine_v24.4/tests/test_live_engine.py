import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from live_engine import CurrentSessionState, SetupCandidate, SetupStatus, preview_current_session

def setup():
    return SetupCandidate('setup_a','ORCL','PRB2','LONG','2025-01-01',10.0,10.0,8.0,14.0,SetupStatus.WATCH,{}, {})
def session(high, freshness=1):
    return CurrentSessionState('2025-01-02',9,high,9,9.5,100,'2025-01-02T15:00:00Z','in_progress','fixture','5m','2025-01-02T15:00:00Z',freshness)
def test_watch_and_ready_preserve_id():
    s=setup(); assert preview_current_session([s],session(9))[0].status is SetupStatus.WATCH
    r=preview_current_session([s],session(11))[0]; assert r.status is SetupStatus.READY and r.setup_id==s.setup_id
def test_stale_never_ready():
    assert preview_current_session([setup()],session(11,9999))[0].status is SetupStatus.STALE_DATA
def test_missing_session_invalid():
    assert preview_current_session([setup()],None)[0].status is SetupStatus.INVALID
