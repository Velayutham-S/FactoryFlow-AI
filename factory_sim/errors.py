"""Exceptions for the Factory Simulation Engine.

One root so a caller can catch the whole layer, and one subclass per cause a
caller could reasonably react to differently.
"""

from __future__ import annotations


class SimulationError(Exception):
    """Root of every error the simulator raises."""


class MasterDataIncompleteError(SimulationError):
    """Master data does not support simulation.

    FACTORY_SQLITE_DATABASE_SCHEMA.md §41.3 lists completeness rules that "gate
    simulator start". A factory with no machines, no lines, no production route,
    or no shifts cannot be simulated, and starting anyway would produce
    operational rows describing a factory that does not exist.
    """


class SimulationStateError(SimulationError):
    """The simulator reached a state its own rules declare impossible.

    Raised rather than repaired. Every case means a generated row would violate a
    documented business rule, and continuing would put invalid operational data in
    the database, which is the one thing this phase must not do.
    """
