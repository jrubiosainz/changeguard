import json

import pytest

from changeguard.provenance import ENTRY_FILES, SOURCE_FOLDERS, source_digest, verified_build


def fixture_tree(tmp_path):
    for name in ENTRY_FILES:
        (tmp_path / name).write_text("synthetic fixture\n")
    for name in SOURCE_FOLDERS:
        (tmp_path / name).mkdir()
    (tmp_path / "changeguard/module.py").write_text("value = 1\n")
    return tmp_path


def test_runtime_identity_is_not_just_an_environment_label(tmp_path):
    root = fixture_tree(tmp_path)
    with pytest.raises(ValueError, match="bundled build identity"):
        verified_build(root)
    identity = {"sourceCommit": "a" * 40, "sourceDigest": source_digest(root)}
    (root / "build-info.json").write_text(json.dumps(identity))
    assert verified_build(root) == identity
    (root / "changeguard/module.py").write_text("value = 2\n")
    with pytest.raises(ValueError, match="do not match"):
        verified_build(root)


def test_evidence_publication_does_not_change_runtime_source_digest(tmp_path):
    root = fixture_tree(tmp_path)
    digest = source_digest(root)
    (root / "evidence").mkdir()
    (root / "evidence/live-run.json").write_text('{"fixture": true}')
    assert source_digest(root) == digest


def test_missing_commit_cannot_be_labeled_live(tmp_path):
    root = fixture_tree(tmp_path)
    (root / "build-info.json").write_text(
        json.dumps({"sourceCommit": "0" * 40, "sourceDigest": source_digest(root)})
    )
    with pytest.raises(ValueError, match="real source commit"):
        verified_build(root)
