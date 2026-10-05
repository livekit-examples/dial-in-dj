import numpy as np

from dj.audio import SAMPLES_PER_FRAME, Ducker, PcmBuffer, stereo_to_mono, to_frame


def test_stereo_to_mono_averages_channels():
    stereo = np.array([100, 300, -200, -400, 32767, 32767], dtype=np.int16)
    mono = stereo_to_mono(stereo.tobytes())
    assert mono.tolist() == [200, -300, 32767]


def test_stereo_to_mono_drops_trailing_half_sample():
    assert stereo_to_mono(np.array([1, 2, 3], dtype=np.int16).tobytes()).size == 1


def test_buffer_pop_exact_across_chunks():
    buf = PcmBuffer()
    buf.push(np.arange(500, dtype=np.int16))
    buf.push(np.arange(500, 1000, dtype=np.int16))
    assert buf.pop(1001) is None
    out = buf.pop(960)
    assert out is not None and out.tolist() == list(range(960))
    assert len(buf) == 40


def test_buffer_cap_drops_oldest():
    buf = PcmBuffer(max_seconds=0.01)  # 480 samples
    buf.push(np.arange(1000, dtype=np.int16))
    assert len(buf) == 480
    assert buf.pop(1)[0] == 520


def test_ducker_ramps_smoothly_to_target():
    d = Ducker(ramp_seconds=0.1)  # full swing over 4800 samples
    d.target = 0.0
    loud = np.full(SAMPLES_PER_FRAME, 10000, dtype=np.int16)
    first = d.apply(loud)
    assert first[0] == 10000 and first[-1] < 10000
    for _ in range(10):
        last = d.apply(loud)
    assert d.current == 0.0 and not last.any()


def test_ducker_passthrough_at_unity():
    x = np.arange(SAMPLES_PER_FRAME, dtype=np.int16)
    assert Ducker().apply(x) is x


def test_frame_shape():
    f = to_frame(np.zeros(SAMPLES_PER_FRAME, dtype=np.int16))
    assert (f.sample_rate, f.num_channels, f.samples_per_channel) == (48000, 1, 960)
