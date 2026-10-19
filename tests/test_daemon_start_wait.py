"""WIN-583: the CLI waits honestly for a slow-starting daemon."""

from talky import cli


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _wait(clock, budget=cli._DAEMON_START_BUDGET_SECS, **kw):
    return cli._wait_for_daemon_ready(
        budget, verbose=False, clock=clock, sleep=clock.sleep,
        pid_alive=kw.pop("pid_alive", lambda: True),
        fetch_readiness=kw.pop("fetch_readiness", lambda: None), **kw,
    )


def test_default_budget_exceeds_old_30s():
    assert cli._DAEMON_START_BUDGET_SECS >= 120


def test_slow_daemon_ready_at_45s_succeeds():
    c = FakeClock()
    result, elapsed = _wait(c, is_ready=lambda: c.t >= 45.0)
    assert result == "ready"
    assert 45.0 <= elapsed < 46.0


def test_dead_process_fails_fast():
    c = FakeClock()
    result, elapsed = _wait(c, is_ready=lambda: False, pid_alive=lambda: c.t < 3.0)
    assert result == "died"
    assert elapsed < 5.0


def test_never_ready_times_out_at_budget():
    c = FakeClock()
    result, elapsed = _wait(c, budget=60.0, is_ready=lambda: False)
    assert result == "timeout"
    assert 60.0 <= elapsed < 61.0


def test_open_readiness_tasks_extend_deadline():
    c = FakeClock()
    tasks = {"tasks": [{"name": "Warming voice channel", "pct": None}]}
    result, _ = _wait(c, budget=30.0, is_ready=lambda: c.t >= 100.0, fetch_readiness=lambda: tasks)
    assert result == "ready"
