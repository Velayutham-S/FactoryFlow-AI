"""Re-exports the 2 system model classes (§44.1).

Both sit in the ``system`` group because their subject is the platform rather than
the factory. That determines which package they live in and nothing else -- all 53
tables share one database file and no name is qualified (§13, §31).
"""

from models.system.audit import AuditLog
from models.system.health import SystemHealthStatus

__all__ = [
    "AuditLog",
    "SystemHealthStatus",
]
