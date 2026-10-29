from esign.adapters.http.rate_limit import FixedWindowLimiter, RateLimitRule, demo_rules


class ManualClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_reports_retry_after() -> None:
    clock = ManualClock(1_200_020.0)  # 20 s into the 60 s window starting at 1_200_000
    limiter = FixedWindowLimiter(now=clock)
    assert [limiter.hit("ip", 3, 60) for _ in range(3)] == [None, None, None]
    assert limiter.hit("ip", 3, 60) == 40


def test_new_window_resets_the_count() -> None:
    clock = ManualClock()
    limiter = FixedWindowLimiter(now=clock)
    for _ in range(4):
        limiter.hit("ip", 3, 60)
    clock.now += 60
    assert limiter.hit("ip", 3, 60) is None


def test_keys_are_counted_separately() -> None:
    limiter = FixedWindowLimiter(now=ManualClock())
    for _ in range(3):
        limiter.hit("a", 3, 60)
    assert limiter.hit("a", 3, 60) is not None
    assert limiter.hit("b", 3, 60) is None


def test_expired_windows_are_pruned() -> None:
    clock = ManualClock()
    limiter = FixedWindowLimiter(now=clock)
    for i in range(10_001):
        limiter.hit(f"ip-{i}", 1, 60)
    clock.now += 7200
    limiter.hit("fresh", 1, 60)
    assert len(limiter._counts) == 1


def test_rule_matching() -> None:
    requests, envelopes = demo_rules(per_minute=30, envelopes_per_hour=10)
    assert requests.matches("GET", "/docs")
    assert envelopes.matches("POST", "/envelopes")
    assert not envelopes.matches("GET", "/envelopes")
    assert not envelopes.matches("POST", "/envelopes/x/send")
    assert RateLimitRule("any", 1, 60).matches("DELETE", "/whatever")
