"""Mix timer math (pass 8): pure functions in engine.py.

Pins the performance timer the MIX readout relies on: elapsed
accumulates only while running (from monotonic deltas the caller
passes in), STOP freezes without arming the auto-start again, RESET
returns to the untouched state, and the remaining readout flips to
a +mm:ss overage exactly at the target boundary. Minutes keep
counting past 59 — a long mix never wraps into hours.
"""
from engine import (DEFAULT_TARGET_MINUTES, format_mix_clock,
                    format_mix_remaining, mix_timer_auto_start,
                    mix_timer_reset, mix_timer_start, mix_timer_stop,
                    mix_timer_update, new_mix_timer)


def test_fresh_timer_is_zeroed_and_stopped():
    timer = new_mix_timer()
    assert timer['elapsed'] == 0.0
    assert timer['running'] is False
    assert mix_timer_update(timer, 100.0) == 0.0


def test_elapsed_accumulates_while_running():
    timer = new_mix_timer()
    mix_timer_start(timer, 10.0)
    assert mix_timer_update(timer, 25.0) == 15.0
    assert mix_timer_update(timer, 25.0) == 15.0   # same instant: no gain
    assert mix_timer_update(timer, 70.5) == 60.5


def test_update_never_goes_backwards():
    timer = new_mix_timer()
    mix_timer_start(timer, 10.0)
    mix_timer_update(timer, 20.0)
    assert mix_timer_update(timer, 5.0) == 10.0    # clock hiccup: clamped


def test_stop_freezes_elapsed():
    timer = new_mix_timer()
    mix_timer_start(timer, 0.0)
    mix_timer_stop(timer, 30.0)                    # accrues up to the stop
    assert timer['elapsed'] == 30.0
    assert timer['running'] is False
    assert mix_timer_update(timer, 90.0) == 30.0   # frozen while stopped


def test_restart_continues_from_frozen_elapsed():
    timer = new_mix_timer()
    mix_timer_start(timer, 0.0)
    mix_timer_stop(timer, 30.0)
    mix_timer_start(timer, 100.0)
    assert mix_timer_update(timer, 110.0) == 40.0


def test_reset_zeroes_and_rearms_auto_start():
    timer = new_mix_timer()
    mix_timer_start(timer, 0.0)
    mix_timer_update(timer, 45.0)
    mix_timer_reset(timer)
    assert timer == new_mix_timer()
    assert mix_timer_auto_start(timer, True, 50.0) is True


def test_auto_start_fires_once_on_first_play():
    timer = new_mix_timer()
    assert mix_timer_auto_start(timer, False, 0.0) is False
    assert timer['running'] is False
    assert mix_timer_auto_start(timer, True, 1.0) is True
    assert timer['running'] is True
    assert mix_timer_auto_start(timer, True, 2.0) is False  # already started


def test_auto_start_does_not_override_a_manual_stop():
    timer = new_mix_timer()
    mix_timer_start(timer, 0.0)
    mix_timer_stop(timer, 20.0)
    # A deck is still playing, but the user stopped the timer on
    # purpose: it must stay frozen until START or RESET.
    assert mix_timer_auto_start(timer, True, 25.0) is False
    assert timer['running'] is False
    assert timer['elapsed'] == 20.0


def test_clock_format_counts_minutes_past_59():
    assert format_mix_clock(0) == '00:00'
    assert format_mix_clock(23 * 60 + 45) == '23:45'
    assert format_mix_clock(59 * 60 + 59) == '59:59'
    assert format_mix_clock(75 * 60) == '75:00'
    assert format_mix_clock(9.9) == '00:09'        # floors to whole seconds


def test_remaining_before_at_and_past_target():
    target = 60 * 60
    assert format_mix_remaining(0, target) == '60:00'
    assert format_mix_remaining(23 * 60 + 45, target) == '36:15'
    assert format_mix_remaining(target, target) == '00:00'   # boundary
    assert format_mix_remaining(target + 1, target) == '+00:01'
    assert format_mix_remaining(target + 150, target) == '+02:30'


def test_default_target_is_sixty_minutes():
    assert DEFAULT_TARGET_MINUTES == 60
    assert format_mix_remaining(0, DEFAULT_TARGET_MINUTES * 60) == '60:00'
