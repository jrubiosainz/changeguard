import hashlib
import json

from changeguard.settings import ROOT


def test_example_retains_exact_measurements_without_claiming_current_execution():
    path = ROOT / "static/examples/recorded-session.json"
    report = json.loads(path.read_text())
    metrics = {item["name"]: item["value"] for item in report["metrics"]}
    assert metrics["decoded_video_frames"] == 606
    assert metrics["total_video_frames"] == 610
    assert metrics["video_width"] == 1920 and metrics["video_height"] == 1080
    assert metrics["inbound_audio_bytes"] == 150615
    assert metrics["browser_response_count"] == 12
    assert metrics["browser_boundary_findings"] == metrics["response_inspection_errors"] == 0
    assert report["browser"] == {"engine": "webkit", "version": "26.6", "location": "local-macos"}
    assert report["provenance"]["recordedAt"] == "2026-09-21T20:12:55.775342+00:00"
    assert "not an execution of this checkout" in report["sourceBinding"]
    assert "sourceCommit" not in report and "runtimeSourceSha256" not in report
    assert len(report["provenance"]["originalReportSha256"]) == 64
    assert hashlib.sha256(path.read_bytes()).hexdigest() != report["provenance"]["originalReportSha256"]
    assert all(item["kind"] == "measured" for item in report["metrics"])
