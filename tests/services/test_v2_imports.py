"""W-02 — confirm v2 module skeletons import cleanly.

Import-time failure would break the whole suite; this isolates it.
"""


def test_scaffold_schema_imports():
    from app.services import scaffold_schema
    assert hasattr(scaffold_schema, "CLASSIFIER_RESPONSE_SCHEMA")
    assert hasattr(scaffold_schema, "STRUCTURED_RESPONSE_SCHEMA")
    assert hasattr(scaffold_schema, "PROVIDER_JSON_MODE")


def test_structured_generator_imports():
    from app.services import structured_generator
    assert hasattr(structured_generator, "stream_structured_response")


def test_scaffold_auditor_imports():
    from app.services import scaffold_auditor
    assert hasattr(scaffold_auditor, "audit_response")


def test_structured_generator_raises_notimplemented():
    """Stub must raise so nothing wires it up before W-15."""
    from app.services.structured_generator import stream_structured_response
    import pytest
    with pytest.raises(NotImplementedError):
        next(iter(stream_structured_response("hi", "gpt-4.1", None)))


def test_scaffold_auditor_raises_notimplemented():
    """Stub must raise so nothing wires it up before W-17."""
    from app.services.scaffold_auditor import audit_response
    import pytest
    with pytest.raises(NotImplementedError):
        audit_response(None, "")
