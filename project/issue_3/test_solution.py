import pytest
from exceptions import (
    DependencyViolationError,
    DuplicateTemplateError,
    ImmutableTemplateError,
    InvalidVersionFormatError,
)
from models import ApplicationComponent, Status
from repository import CatalogRepository


def test_create_and_get_latest():
    repo = CatalogRepository()
    comp1 = ApplicationComponent("app1", "Component", "1.0.0", {"key": "val"})
    comp2 = ApplicationComponent("app1", "Component", "2.0.0", {"key": "val2"})
    comp3 = ApplicationComponent("app1", "Component", "1.5.0", {"key": "val3"})

    repo.create(comp1)
    repo.create(comp2)
    repo.create(comp3)

    latest = repo.get_latest("app1")
    assert latest.version == "2.0.0"


def test_version_collision():
    repo = CatalogRepository()
    comp = ApplicationComponent("app1", "Component", "1.0.0", {})
    repo.create(comp)
    with pytest.raises(DuplicateTemplateError):
        repo.create(comp)


def test_immutability_violation():
    repo = CatalogRepository()
    comp = ApplicationComponent("app1", "Component", "1.0.0", {}, status=Status.RELEASED)
    repo.create(comp)
    with pytest.raises(ImmutableTemplateError):
        repo.update("app1", "1.0.0", {"new": "spec"})


def test_invalid_semver():
    repo = CatalogRepository()
    # "1.0" is technically valid semver in packaging, but "v1" is not.
    # Let's use something definitely invalid.
    with pytest.raises(InvalidVersionFormatError):
        repo.create(ApplicationComponent("app1", "Component", "invalid-version", {}))


def test_dependency_blocking():
    repo = CatalogRepository()
    comp = ApplicationComponent("app1", "Component", "1.0.0", {}, status=Status.RELEASED)
    repo.create(comp)

    # Mock is_referenced to return True
    repo.is_referenced = lambda id, version: True

    with pytest.raises(DependencyViolationError):
        repo.delete("app1", "1.0.0")


def test_serialization():
    comp = ApplicationComponent("app1", "Component", "1.0.0", {"a": 1})
    json_str = comp.to_json()
    new_comp = ApplicationComponent.from_json(json_str)
    assert new_comp.id == comp.id
    assert new_comp.status == comp.status
