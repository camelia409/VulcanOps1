from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MachineCriticality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MachineStatus(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    UNDER_MAINTENANCE = "under_maintenance"
    OFFLINE = "offline"
    DECOMMISSIONED = "decommissioned"


class MaintenancePriority(str, Enum):
    ROUTINE = "routine"
    SCHEDULED = "scheduled"
    URGENT = "urgent"
    EMERGENCY = "emergency"
