from agentqueue.clock import FakeClock, RealClock


def test_fake_clock_moves_only_by_hand():
    c = FakeClock(10)
    assert c.now() == 10
    c.sleep(2.5)
    c.advance(1)
    assert c.now() == 13.5


def test_real_clock_moves():
    c = RealClock()
    a = c.now()
    c.sleep(0.01)
    assert c.now() > a
