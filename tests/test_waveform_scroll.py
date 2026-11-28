"""Scrolling waveform window math (pass 7): pure functions in engine.py.

Pins the rekordbox-style behaviour the deck waveform relies on: a
WAVE_WINDOW_SECONDS window centred on the play position (playhead
fixed at the centre, track edges scroll through unclamped), the peak
buckets intersecting the window, and click-to-seek fractions for both
the scrolling area and the overview strip.
"""
from engine import (RATE, WAVE_WINDOW_SECONDS, frame_to_x, peak_span,
                    scroll_window, seek_fraction)

TOTAL = RATE * 240          # a 4-minute track, in frames
OVERVIEW = 16               # overview strip height used by the widget


def test_window_is_centred_on_position():
    start, end = scroll_window(60 * RATE)
    assert (start + end) / 2 == 60 * RATE
    assert end - start == WAVE_WINDOW_SECONDS * RATE


def test_window_edges_are_not_clamped():
    start, _ = scroll_window(0)
    assert start == -WAVE_WINDOW_SECONDS / 2 * RATE
    _, end = scroll_window(TOTAL)
    assert end == TOTAL + WAVE_WINDOW_SECONDS / 2 * RATE


def test_position_maps_to_centre_pixel():
    start, end = scroll_window(60 * RATE)
    assert frame_to_x(60 * RATE, start, end, 600) == 300
    assert frame_to_x(start, start, end, 600) == 0
    assert frame_to_x(end, start, end, 600) == 600


def test_peak_span_covers_the_window():
    count = 1400
    start, end = scroll_window(60 * RATE)
    lo, hi = peak_span(count, TOTAL, start, end)
    assert lo == int(start * count / TOTAL)
    assert hi == int(end * count / TOTAL) + 1
    assert 0 <= lo < hi <= count


def test_peak_span_at_track_edges():
    count = 1400
    start, end = scroll_window(0)
    lo, hi = peak_span(count, TOTAL, start, end)
    assert lo == 0 and 0 < hi <= count
    start, end = scroll_window(TOTAL)
    lo, hi = peak_span(count, TOTAL, start, end)
    assert hi == count and 0 <= lo < count


def test_peak_span_empty_inputs():
    assert peak_span(0, TOTAL, 0, 100) == (0, 0)
    assert peak_span(1400, 0, 0, 100) == (0, 0)


def test_seek_in_overview_uses_track_fraction():
    # y inside the bottom strip -> plain fraction of the whole track.
    frac = seek_fraction(150, 100, 600, 104, OVERVIEW, 60 * RATE, TOTAL)
    assert frac == 0.25


def test_seek_in_scrolling_area_uses_window_time():
    # Centre of the scrolling area is the play position itself.
    pos = 60 * RATE
    frac = seek_fraction(300, 10, 600, 104, OVERVIEW, pos, TOTAL)
    assert frac == pos / TOTAL
    # Left edge of the window is 8 s before the position.
    frac = seek_fraction(0, 10, 600, 104, OVERVIEW, pos, TOTAL)
    assert frac == (pos - WAVE_WINDOW_SECONDS / 2 * RATE) / TOTAL


def test_seek_fraction_is_clamped():
    # Window hanging off the track start: clicking its left half is < 0.
    frac = seek_fraction(0, 10, 600, 104, OVERVIEW, 0, TOTAL)
    assert frac == 0.0
    frac = seek_fraction(600, 100, 600, 104, OVERVIEW, 60 * RATE, TOTAL)
    assert frac == 1.0
