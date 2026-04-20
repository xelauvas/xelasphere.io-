from mempalace_sidecar.version import __version__

def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
