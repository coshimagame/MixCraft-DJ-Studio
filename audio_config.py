"""Output selection independent of UI, testable without a physical device.

Pass 23 (user direction, 2026-11-27: 「音もRBのASIOとおさないとぶつ
ぶつなる」): the 'auto' selection is now a ladder —
  1. the DDJ-RB on its ASIO host (the controller's own low-latency
     driver path; what the user asked the sound to go through),
  2. the DDJ-RB on WASAPI (the same hardware through Windows' API
     when no ASIO device is visible — pass 24 ships an ASIO
     PortAudio in sounddevice's -asio slot, so rung 1 is live
     whenever the RB's ASIO driver is installed),
  3. the WASAPI default output device (the pre-pass-23 behaviour),
  4. the system default.
Explicit selections (a device index, or None for the system
default) are always respected as-is.
"""


def list_outputs(audio):
    """Every output-capable device as a plain dict: index, name,
    hostapi (name), max_output_channels. Defensive by design: a
    backend whose device records lack the expected fields yields
    an empty list rather than an exception, and the caller falls
    back to the legacy host-default logic."""
    try:
        hosts = audio.query_hostapis()
        devices = audio.query_devices()
    except Exception:
        return []
    out = []
    for index, d in enumerate(devices):
        try:
            if d['max_output_channels'] < 1:
                continue
            out.append(dict(index=index, name=d['name'],
                            hostapi=hosts[d['hostapi']]['name'],
                            max_output_channels=d['max_output_channels']))
        except Exception:
            continue
    return out


def _find(devices, host_part, name_part=None):
    for d in devices:
        if host_part not in d['hostapi']:
            continue
        if name_part is not None and name_part not in d['name'].lower():
            continue
        return d
    return None


def pick_auto(audio):
    """The 'auto' ladder -> (device_index_or_None, hostapi_name).
    A None device means sounddevice's system default."""
    devices = list_outputs(audio)
    hit = _find(devices, 'ASIO', 'ddj-rb')
    if hit is not None:
        return hit['index'], hit['hostapi']
    hit = _find(devices, 'WASAPI', 'ddj-rb')
    if hit is not None:
        return hit['index'], hit['hostapi']
    hosts = audio.query_hostapis()
    for h in hosts:
        if 'WASAPI' in h['name'] and h['default_output_device'] >= 0:
            return h['default_output_device'], h['name']
    return None, ''


def asio_ddj_rb_channels(audio):
    """Output channel count of the DDJ-RB's ASIO device, or None
    when no such device is visible. Pass 23 report point: a count
    of 4+ means the channels 3/4 pair exists for a future
    headphone cue bus — this pass only observes it."""
    hit = _find(list_outputs(audio), 'ASIO', 'ddj-rb')
    return hit['max_output_channels'] if hit is not None else None


def resolve_output(selection, audio):
    """-> (device_index_or_None, hostapi_name) for a combo
    selection: 'auto', an explicit device index, or None (the
    system default)."""
    hosts = audio.query_hostapis()
    if selection == 'auto':
        device, hostapi_name = pick_auto(audio)
        if device is not None:
            return device, hostapi_name
        info = audio.query_devices(kind='output')
        return None, hosts[info['hostapi']]['name']
    device = selection
    info = audio.query_devices(device) if device is not None \
        else audio.query_devices(kind='output')
    return device, hosts[info['hostapi']]['name']
