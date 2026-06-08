"""
models.py
─────────
Plain dataclasses that represent SolarEdge entities.
No logic here — just typed containers used throughout the project.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Optimizer:
    serial: str
    name: str            # e.g. "Optimizer 1"
    string_index: int    # which string it belongs to (0-based)


@dataclass
class StringGroup:
    """
    A 'string' in SolarEdge terminology is a chain of optimizers
    attached to one MPPT input on an inverter.
    Named like: String 1.0, String 1.1, String 2.0 …
    """
    name: str              # e.g. "String 1.0"
    inverter_serial: str
    optimizers: list[Optimizer] = field(default_factory=list)


@dataclass
class Inverter:
    serial: str
    name: str              # e.g. "Inverter 1"
    model: str
    site_id: int
    strings: list[StringGroup] = field(default_factory=list)


@dataclass
class Site:
    id: int
    name: str
    status: str
    peak_power: float
    timezone: str = "UTC"
    inverters: list[Inverter] = field(default_factory=list)


@dataclass
class ExportResult:
    """The outcome of one CSV export attempt for a single inverter."""
    site_id: int
    site_name: str
    inverter_serial: str
    inverter_name: str
    status: str          # "SUCCESS" | "FAILED" | "SKIPPED"
    file_path: str = ""
    error: str = ""
    strings_found: int = 0
    optimizers_found: int = 0
