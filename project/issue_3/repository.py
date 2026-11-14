import threading
from typing import Any

from exceptions import (
    DependencyViolationError,
    DuplicateTemplateError,
    ImmutableTemplateError,
    InvalidVersionFormatError,
)
from models import ApplicationComponent, Status
from packaging import version as semver


class CatalogRepository:
    def __init__(self):
        self._storage: dict[tuple[str, str], ApplicationComponent] = {}
        self._lock = threading.RLock()

    def _is_valid_semver(self, v: str) -> bool:
        try:
            semver.Version(v)
            return True
        except (semver.InvalidVersion, TypeError):
            return False

    def is_referenced(self, id: str, version: str) -> bool:
        """Stub for external dependency check."""
        return False

    def create(self, template: ApplicationComponent):
        if not self._is_valid_semver(template.version):
            raise InvalidVersionFormatError(f"Invalid SemVer: {template.version}")

        with self._lock:
            key = (template.id, template.version)
            if key in self._storage:
                raise DuplicateTemplateError(f"Template {key} already exists.")
            self._storage[key] = template

    def get_latest(self, id: str) -> ApplicationComponent | None:
        with self._lock:
            versions = [v for (tid, v) in self._storage if tid == id]
            if not versions:
                return None
            latest_v = max(versions, key=semver.Version)
            return self._storage[(id, latest_v)]

    def update(self, id: str, version: str, new_spec: dict[str, Any]):
        with self._lock:
            template = self._storage.get((id, version))
            if not template:
                raise ValueError(f"Template {id} version {version} not found.")
            if template.status != Status.DRAFT:
                raise ImmutableTemplateError("Only Draft templates can be updated.")
            template.spec = new_spec

    def delete(self, id: str, version: str):
        with self._lock:
            if self.is_referenced(id, version):
                raise DependencyViolationError("Cannot delete referenced template.")

            template = self._storage.get((id, version))
            if template:
                template.is_deleted = True

    def get_all_versions(self, id: str) -> list[ApplicationComponent]:
        with self._lock:
            return [
                t
                for (tid, v), t in self._storage.items()
                if tid == id and not t.is_deleted
            ]
