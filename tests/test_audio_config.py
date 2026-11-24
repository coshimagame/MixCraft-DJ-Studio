from audio_config import resolve_output

class FakeAudio:
    def query_hostapis(self):
        return [dict(name='MME',default_output_device=1),dict(name='Windows WASAPI',default_output_device=5)]
    def query_devices(self,device=None,kind=None):return dict(hostapi=1 if device==5 else 0)

def test_auto_selects_wasapi_default():
    assert resolve_output('auto',FakeAudio())==(5,'Windows WASAPI')

def test_explicit_device_and_system_default_respected():
    assert resolve_output(1,FakeAudio())==(1,'MME')
    assert resolve_output(None,FakeAudio())==(None,'MME')
