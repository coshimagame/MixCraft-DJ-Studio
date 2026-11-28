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

Pass 25 (user report, 2026-11-28: 「RBのへっどふぉんとかは使えな
い PC本体から音出る」): on his machine the ladder fell all the way
to the PC's own speakers — neither RB rung caught. Two changes:
the RB name match is broader (Pioneer's ASIO driver does not
promise the exact 'DDJ-RB' string — any ASIO device naming a DDJ
or Pioneer counts, as do 'DDJ RB' / 'DDJ_RB' spellings on other
hosts), and the ladder is DIAGNOSABLE: diagnose_ladder() reports
every rung's state and every RB device seen, so a failure says
WHY (not present vs present-but-unopened) instead of vanishing.

Pass 37 (user, 2026-11-28: 「ASIOをRBのにできてないのなおっ
た？ヘッドホンもそこからでないし」): the ladder grows downward
from the 4-channel paths — plan_auto() orders every topology
the hardware might offer (one 4ch stream, two WASAPI endpoints
as a master+cue pair, a lone RB endpoint at 2ch) ahead of the
PC's own outputs, and the engine grew a real cue bus to feed
the second pair. The diagnostics name the new rungs too, so the
pass-36 export shows which topology was available and tried.
"""


def rb_name_match(name, hostapi=''):
    """Is this output device the DDJ-RB (pass 25 broadening)?

    Matches, case-insensitively:
    - any name containing 'ddj' — 'DDJ-RB', 'DDJ RB', 'DDJ_RB',
      and the bare 'PIONEER DDJ-RB' spellings all land here;
    - on the ASIO host, any name containing 'pioneer' (the ASIO
      driver may register under the maker's name alone);
    - on other hosts, 'pioneer' together with 'rb' (a Pioneer
      device that is not the RB should not steal the rung)."""
    norm = (name or '').lower()
    if 'ddj' in norm:
        return True
    if 'pioneer' in norm:
        if 'asio' in (hostapi or '').lower():
            return True
        if 'rb' in norm:
            return True
    return False


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


def _find_rb(devices, host_part):
    for d in devices:
        if host_part not in d['hostapi']:
            continue
        if rb_name_match(d['name'], d['hostapi']):
            return d
    return None


def rb_outputs(devices):
    """Every output device that names the DDJ-RB, any host."""
    return [d for d in devices
            if rb_name_match(d['name'], d['hostapi'])]


def pick_auto(audio):
    """The 'auto' ladder -> (device_index_or_None, hostapi_name).
    A None device means sounddevice's system default."""
    devices = list_outputs(audio)
    hit = _find_rb(devices, 'ASIO')
    if hit is not None:
        return hit['index'], hit['hostapi']
    hit = _find_rb(devices, 'WASAPI')
    if hit is not None:
        return hit['index'], hit['hostapi']
    hosts = audio.query_hostapis()
    for h in hosts:
        if 'WASAPI' in h['name'] and h['default_output_device'] >= 0:
            return h['default_output_device'], h['name']
    return None, ''


def plan_auto(audio):
    """The 'auto' output plan, richest first (pass 37): an
    ordered list of topologies to try, so the sound lands on
    the DDJ-RB whenever the hardware offers any path — and,
    when the hardware offers four channels, the headphone cue
    bus comes with it. Each plan is a dict:

      kind     'single4' | 'paired' | 'master_only' | 'default'
      device   master output device index (None = system default)
      cue_device  paired plans only: the second endpoint
      hostapi  host name of the master device ('' for default)
      channels channels to open on the master device

    Order: a 4-channel RB (ASIO first, then WASAPI) as ONE
    stream — master on 1/2, cue on 3/4; then two WASAPI RB
    endpoints as a pair (master on the first, cue on the
    second, started together); then any RB endpoint at 2
    channels (the controller makes the sound; no cue pair
    exists in that mode); then the WASAPI default device; then
    the system default. pick_auto() above stays the single-
    device summary of this same ladder for older callers."""
    devices = list_outputs(audio)
    plans = []
    asio_rb = _find_rb(devices, 'ASIO')
    wasapi_rbs = [d for d in devices
                  if 'WASAPI' in d['hostapi']
                  and rb_name_match(d['name'], d['hostapi'])]
    wasapi_rb = wasapi_rbs[0] if wasapi_rbs else None
    for hit in (asio_rb, wasapi_rb):
        if hit is not None and hit['max_output_channels'] >= 4:
            plans.append(dict(kind='single4', device=hit['index'],
                              cue_device=None, hostapi=hit['hostapi'],
                              channels=4))
    if len(wasapi_rbs) >= 2:
        plans.append(dict(kind='paired', device=wasapi_rbs[0]['index'],
                          cue_device=wasapi_rbs[1]['index'],
                          hostapi=wasapi_rbs[0]['hostapi'],
                          channels=2))
    for hit in (asio_rb, wasapi_rb):
        if hit is not None:
            plans.append(dict(kind='master_only',
                              device=hit['index'], cue_device=None,
                              hostapi=hit['hostapi'], channels=2))
    try:
        hosts = audio.query_hostapis()
    except Exception:
        hosts = []
    for h in hosts:
        if 'WASAPI' in h['name'] and h['default_output_device'] >= 0:
            plans.append(dict(kind='default',
                              device=h['default_output_device'],
                              cue_device=None, hostapi=h['name'],
                              channels=2))
            break
    plans.append(dict(kind='default', device=None, cue_device=None,
                      hostapi='', channels=2))
    return plans


def diagnose_ladder(audio):
    """The auto ladder, rung by rung (pass 25): which RB devices
    are visible on which hosts, which rung the pick lands on, and
    which rungs found nothing. Pure observation — opening devices
    (and recording open errors) is the caller's job; this is the
    'not present' half of every diagnosis. JSON-friendly."""
    devices = list_outputs(audio)
    rb = rb_outputs(devices)
    try:
        host_names = [h['name'] for h in audio.query_hostapis()]
    except Exception:
        host_names = []
    try:
        device, hostapi_name = pick_auto(audio)
    except Exception:
        device, hostapi_name = None, ''
    asio_rb = _find_rb(devices, 'ASIO')
    wasapi_rb = _find_rb(devices, 'WASAPI')
    rungs = [
        dict(rung='asio_rb',
             status='selected' if asio_rb is not None
                    and device == asio_rb['index'] else
                    ('present' if asio_rb is not None else 'not_present'),
             device=asio_rb['index'] if asio_rb else None,
             name=asio_rb['name'] if asio_rb else None),
        dict(rung='wasapi_rb',
             status='selected' if wasapi_rb is not None
                    and device == wasapi_rb['index'] else
                    ('present' if wasapi_rb is not None else 'not_present'),
             device=wasapi_rb['index'] if wasapi_rb else None,
             name=wasapi_rb['name'] if wasapi_rb else None),
        dict(rung='rb_4ch',
             status='present' if any(
                 d['max_output_channels'] >= 4 for d in rb)
                    else 'not_present',
             device=next((d['index'] for d in rb
                          if d['max_output_channels'] >= 4), None),
             name=next((d['name'] for d in rb
                        if d['max_output_channels'] >= 4), None)),
        dict(rung='paired_rb',
             status='present' if len([
                 d for d in rb if 'WASAPI' in d['hostapi']]) >= 2
                    else 'not_present',
             device=None, name=None),
        dict(rung='wasapi_default',
             status='selected' if device is not None
                    and hostapi_name and 'WASAPI' in hostapi_name
                    and device not in [d['index'] for d in rb]
                    else 'not_selected',
             device=device if hostapi_name and 'WASAPI' in hostapi_name
                    else None,
             name=None),
        dict(rung='system_default',
             status='selected' if device is None else 'not_selected',
             device=None, name=None),
    ]
    return dict(hosts=host_names, devices=devices, rb_devices=rb,
                pick=dict(device=device, hostapi=hostapi_name),
                rungs=rungs)


def asio_ddj_rb_channels(audio):
    """Output channel count of the DDJ-RB's ASIO device, or None
    when no such device is visible. Pass 23 report point: a count
    of 4+ means the channels 3/4 pair exists for a future
    headphone cue bus — this pass only observes it."""
    hit = _find_rb(list_outputs(audio), 'ASIO')
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
