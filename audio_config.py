"""Output selection independent of UI, testable without a physical device."""
def resolve_output(selection, audio):
    hosts = audio.query_hostapis()
    device = selection
    if selection == 'auto':
        device = next((h['default_output_device'] for h in hosts
                       if 'WASAPI' in h['name'] and h['default_output_device'] >= 0), None)
    info = audio.query_devices(device) if device is not None else audio.query_devices(kind='output')
    return device, hosts[info['hostapi']]['name']
