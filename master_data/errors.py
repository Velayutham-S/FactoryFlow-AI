"""Exceptions for the master data layer.

Every failure mode this layer can encounter gets a named exception carrying the
dataset and the reason. The layer never returns a boolean for a failure and never
logs-and-continues: a master dataset that did not load is a database that cannot
be trusted, so the failure has to reach the caller.

The hierarchy is deliberately shallow. One root so a caller can catch the whole
layer, and one subclass per distinguishable cause so a caller that wants to react
to a missing file differently from a duplicate key can do so.
"""

from __future__ import annotations


class MasterDataError(Exception):
    """Root of every error this layer raises."""


class DatasetFileError(MasterDataError):
    """A dataset file is missing, unreadable, empty, or not decodable as UTF-8."""


class DatasetStructureError(MasterDataError):
    """A dataset file's header is wrong.

    Raised for a missing header, a duplicated column, an unknown column, a
    missing required column, or a row whose field count does not match the
    header. These are file-level defects: the whole dataset is rejected rather
    than individual rows, because a shifted or misnamed column silently
    misassigns every value in the file.
    """


class RecordValidationError(MasterDataError):
    """One or more records in a dataset failed validation.

    Carries the full list of rejections so the caller reports every one rather
    than only the first. Each rejection names the source line, the column, and
    the reason.
    """

    def __init__(self, table: str, rejections: list[str]) -> None:
        self.table = table
        self.rejections = rejections
        super().__init__(
            "%s: %d record(s) rejected by validation:\n  %s"
            % (table, len(rejections), "\n  ".join(rejections))
        )


class DuplicateRecordError(MasterDataError):
    """A duplicate key was detected before insertion.

    Duplicates are neither skipped nor overwritten. Skipping loses a record the
    author intended to load and overwriting destroys one already loaded, and
    both are silent. The import stops and names every duplicate instead.
    """

    def __init__(self, table: str, findings: list[str]) -> None:
        self.table = table
        self.findings = findings
        super().__init__(
            "%s: %d duplicate key(s) detected before insertion:\n  %s"
            % (table, len(findings), "\n  ".join(findings))
        )


class ForeignKeyResolutionError(MasterDataError):
    """A foreign key reference in a dataset names a parent row that does not exist."""

    def __init__(self, table: str, failures: list[str]) -> None:
        self.table = table
        self.failures = failures
        super().__init__(
            "%s: %d foreign key reference(s) could not be resolved:\n  %s"
            % (table, len(failures), "\n  ".join(failures))
        )


class DatasetImportError(MasterDataError):
    """The insert for one dataset failed and its transaction was rolled back."""


class ImportVerificationError(MasterDataError):
    """Post-import verification failed.

    Raised for a row count that does not match what was imported, an orphaned
    foreign key, an empty table that should hold rows, or a failed integrity
    check.
    """

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__(
            "%d verification check(s) failed:\n  %s"
            % (len(failures), "\n  ".join(failures))
        )


class LoadOrderError(MasterDataError):
    """The configured load order does not satisfy the foreign key graph.

    A guard against the load order and the schema drifting apart: if a dataset is
    scheduled before one of its parents, that is caught before any file is read
    rather than as a foreign key failure partway through a run.
    """
