def test_package_imports():
    import app.mempalace
    assert hasattr(app.mempalace, "__version__")
