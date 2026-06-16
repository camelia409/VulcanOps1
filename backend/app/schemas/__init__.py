from app.schemas.machine import MachineSchema, MachineCreate
from app.schemas.sensor_reading import SensorReadingSchema, SensorReadingCreate
from app.schemas.maintenance_record import MaintenanceRecordSchema, MaintenanceRecordCreate
from app.schemas.report import ReportSchema, EngineerReport, SupervisorReport, ManagerReport

__all__ = [
    "MachineSchema",
    "MachineCreate",
    "SensorReadingSchema",
    "SensorReadingCreate",
    "MaintenanceRecordSchema",
    "MaintenanceRecordCreate",
    "ReportSchema",
    "EngineerReport",
    "SupervisorReport",
    "ManagerReport",
]
