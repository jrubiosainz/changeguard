import importlib.util
import sys
from unittest.mock import Mock

import pytest

from changeguard.settings import ROOT


def load_launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("standalone_launcher", ROOT / "scripts/run_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = Mock()
    monkeypatch.setattr(module, "create_app", Mock(return_value=app))
    monkeypatch.setattr(
        sys.modules["common"], "secret", Mock(side_effect=AssertionError("Preview must not load credentials"))
    )
    monkeypatch.setenv("CHANGEGUARD_MODE", "live-azure")
    return module, app


def test_local_launcher_needs_no_credentials_and_forces_offline(monkeypatch, capsys):
    module, app = load_launcher(monkeypatch)
    module.main([])
    assert module.os.environ["CHANGEGUARD_MODE"] == "local-fixture"
    assert "no operator login or Azure connection" in capsys.readouterr().out
    app.run.assert_called_once_with(host="127.0.0.1", port=8033, debug=False, use_reloader=False)


def test_preview_ignores_unrelated_live_operator_configuration(monkeypatch, capsys):
    module, _ = load_launcher(monkeypatch)
    token = "synthetic-local-operator-fixture-not-a-service-token"
    monkeypatch.setenv("CHANGEGUARD_OPERATOR_TOKEN", token)
    module.main([])
    assert token not in capsys.readouterr().out
    sys.modules["common"].secret.assert_not_called()


@pytest.mark.parametrize("port", ["0", "-1", "65536", "not-a-number"])
def test_local_launcher_rejects_invalid_ports(monkeypatch, port):
    module, app = load_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        module.main(["--port", port])
    app.run.assert_not_called()


def test_local_launcher_supports_an_explicit_loopback_port(monkeypatch):
    module, app = load_launcher(monkeypatch)
    module.main(["--port", "8047"])
    assert module.os.environ["PUBLIC_ORIGIN"] == "http://127.0.0.1:8047"
    app.run.assert_called_once_with(host="127.0.0.1", port=8047, debug=False, use_reloader=False)
