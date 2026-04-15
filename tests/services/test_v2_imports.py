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


# W-15 + W-17 landed: structured_generator and scaffold_auditor are
# now implemented. Their original "stub raises NotImplementedError"
# guards are retired. Behavior is covered by their dedicated test
# files (test_structured_generator.py, test_scaffold_auditor.py).
