"""Import-smoke tests for ProjectService.

The data layer is exercised end-to-end via route tests in
tests/unit/test_project_routes.py. This file just guards the public
surface: constants, class import, method names.
"""
from app.project_service import (
    ProjectService,
    MAX_FILES_PER_PROJECT,
    PER_FILE_TEXT_CAP,
    ALLOWED_FIELDS,
)


def test_constants_have_expected_values():
    assert MAX_FILES_PER_PROJECT == 20
    assert PER_FILE_TEXT_CAP == 200_000
    assert ALLOWED_FIELDS == {'name', 'color', 'instructions', 'position'}


def test_service_public_surface_present():
    svc = ProjectService(db=None)
    for method in ['list', 'get', 'count', 'create', 'update', 'delete',
                   'add_file', 'remove_file', 'list_files']:
        assert callable(getattr(svc, method, None)), f'missing: {method}'


def test_limit_exceeded_is_subclass_of_exception():
    assert issubclass(ProjectService.LimitExceeded, Exception)
