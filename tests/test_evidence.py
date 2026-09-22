from changeguard.evidence import audio_increased, playback_metrics


def test_missing_media_observations_are_not_zero_measurements():
    assert playback_metrics({}, {}) == []
    assert playback_metrics({"firstFrameMs": None}, {}) == []
    assert not audio_increased({}, {})


def test_audio_may_be_silent_before_synthesis_starts():
    assert audio_increased({"inboundAudioBytes": 0}, {"inboundAudioBytes": 200})
    assert not audio_increased({"inboundAudioBytes": 0}, {"inboundAudioBytes": 0})
    assert not audio_increased({"inboundAudioBytes": 200}, {"inboundAudioBytes": 200})


def test_only_actual_values_become_media_metrics():
    values = playback_metrics(
        {"decodedFrames": 42, "inboundAudioBytes": 128, "firstFrameMs": 1234.567},
        {"width": 1920, "height": 1080, "frames": 41},
    )
    assert len(values) == 6
    assert {item["kind"] for item in values} == {"measured"}
    assert next(item for item in values if item["name"] == "first_frame_latency")["value"] == 1234.57
