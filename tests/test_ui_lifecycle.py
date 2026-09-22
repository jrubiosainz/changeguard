import shutil
import subprocess

from changeguard.settings import ROOT


def test_actual_javascript_lifecycle_regressions():
    node = shutil.which("node")
    assert node is not None, "Node 20+ is required for the offline JavaScript lifecycle regressions"
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests/ui_lifecycle.cjs")],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
