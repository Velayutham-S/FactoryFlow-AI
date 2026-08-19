"""Master Data Management for FactoryFlow AI (Phase 2).

Six responsibilities, one module each:

============================  ==========================================
:mod:`master_data.seeder`     orchestration -- the full load, in order
:mod:`master_data.csv_source` CSV reading and structural validation
:mod:`master_data.validation` record validation, derived from the schema
:mod:`master_data.duplicates` duplicate detection before insertion
:mod:`master_data.importer`   foreign key resolution and batch insert
:mod:`master_data.verification` post-import verification
============================  ==========================================

:mod:`master_data.datasets` holds the load order and the CSV column contract;
:mod:`master_data.errors` holds the exceptions.

The layer owns no schema, no engine and no session lifecycle. It reads the frozen
``models`` package for its models and metadata and calls
``models.session.initialize_database``, ``session_scope`` and ``shutdown_database``
for everything to do with connecting and committing.

Typical use::

    from master_data import seed_master_data

    report = seed_master_data(r"C:\\factoryflow\\factoryflow.db", r"data\\master")
    print(report.total_imported)

or from the command line::

    python -m master_data <database-path> <master-data-directory>
"""

from master_data.csv_source import SourceRow, dataset_path, read_dataset
from master_data.datasets import (
    LAYERS,
    DatasetSpec,
    ForeignKeyRef,
    master_datasets,
    verify_load_order,
)
from master_data.duplicates import (
    detect_against_database,
    detect_within_dataset,
)
from master_data.errors import (
    DatasetFileError,
    DatasetImportError,
    DatasetStructureError,
    DuplicateRecordError,
    ForeignKeyResolutionError,
    ImportVerificationError,
    LoadOrderError,
    MasterDataError,
    RecordValidationError,
)
from master_data.importer import DatasetImport, import_dataset
from master_data.seeder import SeedReport, dataset_contract, main, seed_master_data
from master_data.validation import validate_row
from master_data.verification import (
    DatasetVerification,
    verify_dataset,
    verify_master_set,
)

__all__ = [
    "LAYERS",
    "DatasetFileError",
    "DatasetImport",
    "DatasetImportError",
    "DatasetSpec",
    "DatasetStructureError",
    "DatasetVerification",
    "DuplicateRecordError",
    "ForeignKeyRef",
    "ForeignKeyResolutionError",
    "ImportVerificationError",
    "LoadOrderError",
    "MasterDataError",
    "RecordValidationError",
    "SeedReport",
    "SourceRow",
    "dataset_contract",
    "dataset_path",
    "detect_against_database",
    "detect_within_dataset",
    "import_dataset",
    "main",
    "master_datasets",
    "read_dataset",
    "seed_master_data",
    "validate_row",
    "verify_dataset",
    "verify_load_order",
    "verify_master_set",
]
