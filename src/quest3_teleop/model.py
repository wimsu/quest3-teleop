"""Versioned, robot-independent Quest hand data types."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


HAND_SIDES: Final[tuple[str, str]] = ("left", "right")

# Stable Python names in the order defined by the WebXR Hand Input module.
JOINT_NAMES: Final[tuple[str, ...]] = (
    "wrist",
    "thumb_metacarpal",
    "thumb_proximal",
    "thumb_distal",
    "thumb_tip",
    "index_metacarpal",
    "index_proximal",
    "index_intermediate",
    "index_distal",
    "index_tip",
    "middle_metacarpal",
    "middle_proximal",
    "middle_intermediate",
    "middle_distal",
    "middle_tip",
    "ring_metacarpal",
    "ring_proximal",
    "ring_intermediate",
    "ring_distal",
    "ring_tip",
    "little_metacarpal",
    "little_proximal",
    "little_intermediate",
    "little_distal",
    "little_tip",
)


@dataclass(frozen=True, slots=True)
class JointPose:
    position_m: tuple[float, float, float]
    rotation_xyzw: tuple[float, float, float, float]
    radius_m: float


@dataclass(frozen=True, slots=True)
class HandSample:
    side: str
    tracked: bool
    joints: Mapping[str, JointPose]

    def __post_init__(self) -> None:
        if self.side not in HAND_SIDES:
            raise ValueError(f"invalid hand side: {self.side!r}")
        object.__setattr__(self, "joints", MappingProxyType(dict(self.joints)))

    @property
    def complete(self) -> bool:
        return self.tracked and all(name in self.joints for name in JOINT_NAMES)


@dataclass(frozen=True, slots=True)
class HeadPose:
    position_m: tuple[float, float, float]
    rotation_xyzw: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class HandFrame:
    """One coherent Quest observation in a WebXR ``local`` reference space.

    ``sequence`` is assigned by the desktop receiver and remains monotonic if
    the browser page reloads. ``source_sequence`` is the browser-local count.
    Freshness must use ``received_monotonic_ns`` rather than wall-clock time.
    """

    sequence: int
    source_sequence: int
    sender_timestamp_ns: int
    received_monotonic_ns: int
    reference_space: str
    hands: Mapping[str, HandSample]
    head: HeadPose | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "hands", MappingProxyType(dict(self.hands)))

    @property
    def both_hands_tracked(self) -> bool:
        return all(side in self.hands and self.hands[side].complete for side in HAND_SIDES)

    def hand(self, side: str) -> HandSample:
        return self.hands[side]

