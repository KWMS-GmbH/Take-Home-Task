"""Fixed supplied scenario allowlist and independent expected outcomes."""

from __future__ import annotations

from dataclasses import dataclass

FILES = (
    "01_happy_path.json",
    "02_duplicate_stop.json",
    "03_reboot_no_stop.json",
    "04_late_stop.json",
    "05_timezone_offsets.json",
    "06_meter_goes_backwards.json",
    "07_two_connections_and_garbage.json",
    "08_blocked_idtag.json",
)


@dataclass(frozen=True)
class Expectation:
    start_wh: int
    stop_wh: int | None
    energy_kwh: float | None
    seconds: int | None
    lifecycle: str = "completed"
    quality: str = "valid"
    provisional: bool = False
    eligible: bool = True
    samples: tuple[tuple[int, int], ...] = ()


EXPECTATIONS = {
    "01_happy_path.json": Expectation(
        1000, 11000, 10.0, 3300, samples=((1200, 4000), (2400, 8000))
    ),
    "02_duplicate_stop.json": Expectation(1000, 11000, 10.0, 3300),
    "03_reboot_no_stop.json": Expectation(
        1000, None, 6.0, None, "interrupted", "valid", True, False, ((1800, 7000),)
    ),
    "04_late_stop.json": Expectation(2000, 6000, 4.0, 3600),
    "05_timezone_offsets.json": Expectation(1000, 9000, 8.0, 2700),
    "06_meter_goes_backwards.json": Expectation(
        500000, 1200, None, 3300, quality="suspicious", eligible=False
    ),
    "07_two_connections_and_garbage.json": Expectation(1000, 4000, 3.0, 3300),
    "08_blocked_idtag.json": Expectation(1000, 1500, 0.5, 3300, eligible=False),
}

CALL_COUNTS = {
    "01_happy_path.json": {
        "BootNotification": 1,
        "Authorize": 1,
        "StatusNotification": 3,
        "StartTransaction": 1,
        "MeterValues": 2,
        "StopTransaction": 1,
    },
    "02_duplicate_stop.json": {"BootNotification": 2, "StartTransaction": 1, "StopTransaction": 2},
    "03_reboot_no_stop.json": {
        "BootNotification": 2,
        "StartTransaction": 1,
        "MeterValues": 1,
        "StatusNotification": 1,
    },
    "04_late_stop.json": {"BootNotification": 2, "StartTransaction": 1, "StopTransaction": 1},
    "05_timezone_offsets.json": {
        "BootNotification": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
    "06_meter_goes_backwards.json": {
        "BootNotification": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
    "07_two_connections_and_garbage.json": {
        "BootNotification": 4,
        "StartTransaction": 2,
        "StopTransaction": 1,
        "DataTransfer": 1,
    },
    "08_blocked_idtag.json": {
        "BootNotification": 1,
        "Authorize": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
}
