from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProcessStartedPayload:
    launch_id: str
    capsule_id: str
    capsule_name: str
    capsule_path: str
    program_name: str
    program_path: str


@dataclass(frozen=True)
class ProcessFinishedPayload:
    launch_id: str
    status: Literal["success", "unknown"]
    ended_at: int  # epoch seconds


