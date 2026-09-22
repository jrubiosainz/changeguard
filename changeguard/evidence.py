def check(identifier: str, passed: bool, details: str) -> dict:
    return {
        "id": identifier,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "details": details,
    }


def metric(name: str, value: float, unit: str, method: str, kind: str = "measured") -> dict:
    return {"name": name, "value": value, "unit": unit, "kind": kind, "method": method}


def playback_metrics(browser: dict, video: dict) -> list[dict]:
    """An unobserved value is absent, never a zero presented as a measurement."""
    values = []
    for source, key, name, unit, method in (
        (
            browser,
            "decodedFrames",
            "decoded_video_frames",
            "frames",
            "RTCPeerConnection.getStats inbound-rtp framesDecoded (engine/version recorded separately)",
        ),
        (
            browser,
            "inboundAudioBytes",
            "inbound_audio_bytes",
            "bytes",
            "RTCPeerConnection.getStats inbound-rtp audio bytesReceived (engine/version recorded separately)",
        ),
        (
            video,
            "frames",
            "total_video_frames",
            "frames",
            "Native HTMLVideoElement.getVideoPlaybackQuality totalVideoFrames (includes dropped frames)",
        ),
        (video, "width", "video_width", "pixels", "Native video.videoWidth"),
        (video, "height", "video_height", "pixels", "Native video.videoHeight"),
    ):
        if key in source:
            values.append(metric(name, source[key], unit, method))
    if browser.get("firstFrameMs") is not None:
        values.append(
            metric(
                "first_frame_latency",
                round(browser["firstFrameMs"], 2),
                "milliseconds",
                "Operator start to first requestVideoFrameCallback",
            )
        )
    return values


def audio_increased(before: dict, after: dict) -> bool:
    return (
        "inboundAudioBytes" in before
        and "inboundAudioBytes" in after
        and after["inboundAudioBytes"] > before["inboundAudioBytes"] >= 0
    )
