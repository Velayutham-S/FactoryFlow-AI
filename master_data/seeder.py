"""The master data seeder: the orchestration for one full load.

One pass over the 29 datasets in §30.3 layer order, and for each one: read, validate,
detect duplicates, import, verify. Parents are always loaded and verified before the
datasets that reference them, so no foreign key is ever written speculatively and
enforcement is never relaxed.

**One transaction per dataset.** Each dataset gets its own
``models.session.session_scope``: it commits when the dataset is complete and rolls
the whole dataset back on any failure. A dataset is the unit because it is the unit
the dependency order is defined over -- committing per row would leave a half-loaded
table that the next dataset's foreign keys would resolve against, and wrapping all 29
in one transaction would mean a defect in the last file discards 28 correct loads.

The run stops at the first dataset that fails. Continuing past a failure would
attempt to resolve children against a parent table that is missing rows, turning one
reportable error into a cascade of misleading ones.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from models.session import (
    initialize_database,
    session_scope,
    shutdown_database,
)

from master_data.csv_source import read_dataset
from master_data.datasets import DatasetSpec, master_datasets, verify_load_order
from master_data.importer import DatasetImport, import_dataset
from master_data.verification import (
    DatasetVerification,
    require,
    verify_dataset,
    verify_master_set,
)

LAYER_TITLES = (
    "Layer 0  Roots",
    "Layer 1  Direct children of roots",
    "Layer 2  Structural and specification entities",
    "Layer 3  Core assets, people and policy",
    "Layer 4  Specializations and cross-domain junctions",
)


@dataclass
class SeedReport:
    """The outcome of one full seed."""

    imports: list[DatasetImport] = field(default_factory=list)
    verifications: list[DatasetVerification] = field(default_factory=list)

    @property
    def total_read(self) -> int:
        return sum(item.read for item in self.imports)

    @property
    def total_imported(self) -> int:
        return sum(item.imported for item in self.imports)

    @property
    def rejections(self) -> list[str]:
        found: list[str] = []
        for item in self.imports:
            found.extend(item.rejections)
        return found


def _reporter(quiet: bool) -> Callable[[str], None]:
    if quiet:
        return lambda _message: None
    return lambda message: print(message, flush=True)


def seed_master_data(
    database_path: str | Path,
    data_directory: str | Path,
    *,
    strict: bool = True,
    quiet: bool = False,
) -> SeedReport:
    """Load every master dataset from ``data_directory`` into the database.

    The database is opened through ``models.session.initialize_database``, which
    creates the schema only when the file is absent and verifies it either way, and
    is closed through ``shutdown_database`` on every path including failure.
    """
    say = _reporter(quiet)
    directory = Path(data_directory)
    if not directory.is_dir():
        raise NotADirectoryError(
            "no master data directory at %s" % directory
        )

    specs = master_datasets()
    verify_load_order(specs)

    report = SeedReport()
    engine, session_factory = initialize_database(database_path)
    try:
        say("Seeding master data from %s" % directory)
        say("%d datasets, dependency layers 0-4" % len(specs))

        current_layer = -1
        for spec in specs:
            if spec.layer != current_layer:
                current_layer = spec.layer
                say("")
                say(LAYER_TITLES[current_layer])

            say("Loading %s..." % spec.title)
            source_rows = read_dataset(spec, directory)

            with session_scope(session_factory) as session:
                dataset_report = import_dataset(
                    spec, session, source_rows, strict=strict
                )
            report.imports.append(dataset_report)

            with session_scope(session_factory) as session:
                verification = verify_dataset(
                    spec, session, dataset_report.imported
                )
            report.verifications.append(verification)
            require(verification.failures)

            say(
                "  %-30s read %-5d imported %-5d verified"
                % (spec.table, dataset_report.read, dataset_report.imported)
            )

        say("")
        say("Verifying the complete master set...")
        require(verify_master_set(engine, session_factory, specs))

        say(
            "Verification Complete. %d datasets, %d records."
            % (len(specs), report.total_imported)
        )
    finally:
        shutdown_database(engine)

    return report


def main(argv: list[str] | None = None) -> int:
    """Command line entry point: ``python -m master_data <database> <data-dir>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: python -m master_data <database-path> <master-data-directory>",
            file=sys.stderr,
        )
        return 2

    database_path = Path(args[0]).resolve()
    directory = Path(args[1]).resolve()
    try:
        seed_master_data(database_path, directory)
    except Exception as exc:
        print("", file=sys.stderr)
        print("Master data load failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0


def dataset_contract(spec: DatasetSpec) -> str:
    """A one-dataset summary of the CSV contract, for diagnostics.

    Printed by the failure path when a file is missing, so the author can see the
    exact column set the schema requires without reading the DDL.
    """
    return "\n".join(
        (
            "%s  ->  %s" % (spec.table, spec.filename),
            "  required: %s" % (", ".join(sorted(spec.required_columns)) or "(none)"),
            "  optional: %s" % (", ".join(sorted(spec.optional_columns)) or "(none)"),
        )
    )
