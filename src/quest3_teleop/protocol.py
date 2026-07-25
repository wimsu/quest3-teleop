"""Validation for the compact browser-to-desktop JSON protocol."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from .model import HAND_SIDES, JOINT_NAMES, HandFrame, HandSample, HeadPose, JointPose


PACKET_TYPE = "quest3.hand_frame.v1"


class HandPacketError(ValueError):
    pass


def _finite_values(
    value: object,
    *,
    length: int,
    label: str,
    limit: float = 20.0,
) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise HandPacketError(f"{label} must contain {length} numbers")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise HandPacketError(f"{label} contains a non-numeric value") from exc
    if not all(math.isfinite(item) and abs(item) <= limit for item in result):
        raise HandPacketError(f"{label} contains an invalid value")
    return result


def _xyz(values: tuple[float, ...], index: int) -> tuple[float, float, float]:
    offset = index * 3
    return values[offset], values[offset + 1], values[offset + 2]


def _xyzw(values: tuple[float, ...], index: int) -> tuple[float, float, float, float]:
    offset = index * 4
    quaternion = values[offset], values[offset + 1], values[offset + 2], values[offset + 3]
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm < 0.5 or norm > 1.5:
        raise HandPacketError("joint quaternion has an implausible norm")
    return tuple(component / norm for component in quaternion)  # type: ignore[return-value]


def _hand(value: object, side: str) -> HandSample:
    if not isinstance(value, Mapping):
        raise HandPacketError(f"{side} must be an object")
    tracked = value.get("tracked") is True
    if not tracked:
        return HandSample(side=side, tracked=False, joints={})
    positions = _finite_values(
        value.get("positions_m"),
        length=len(JOINT_NAMES) * 3,
        label=f"{side}.positions_m",
    )
    rotations = _finite_values(
        value.get("rotations_xyzw"),
        length=len(JOINT_NAMES) * 4,
        label=f"{side}.rotations_xyzw",
        limit=2.0,
    )
    radii = _finite_values(
        value.get("radii_m"),
        length=len(JOINT_NAMES),
        label=f"{side}.radii_m",
        limit=0.1,
    )
    if all(abs(component) < 1e-10 for component in positions):
        raise HandPacketError(f"{side} positions are unavailable")
    if any(radius < 0.0 for radius in radii):
        raise HandPacketError(f"{side} contains a negative joint radius")
    joints = {
        name: JointPose(
            position_m=_xyz(positions, index),
            rotation_xyzw=_xyzw(rotations, index),
            radius_m=radii[index],
        )
        for index, name in enumerate(JOINT_NAMES)
    }
    return HandSample(side=side, tracked=True, joints=joints)


def _head(value: object) -> HeadPose | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HandPacketError("head must be null or an object")
    position = _finite_values(value.get("position_m"), length=3, label="head.position_m")
    rotation = _finite_values(
        value.get("rotation_xyzw"),
        length=4,
        label="head.rotation_xyzw",
        limit=2.0,
    )
    norm = math.sqrt(sum(component * component for component in rotation))
    if norm < 0.5 or norm > 1.5:
        raise HandPacketError("head quaternion has an implausible norm")
    return HeadPose(
        position_m=(position[0], position[1], position[2]),
        rotation_xyzw=tuple(component / norm for component in rotation),  # type: ignore[arg-type]
    )


def parse_hand_frame(value: object, *, sequence: int, received_monotonic_ns: int) -> HandFrame:
    if not isinstance(value, Mapping):
        raise HandPacketError("packet must be a JSON object")
    if value.get("type") != PACKET_TYPE:
        raise HandPacketError("unexpected packet type")
    source_sequence = value.get("sequence")
    sender_timestamp_ns = value.get("sender_timestamp_ns")
    if not isinstance(source_sequence, int) or source_sequence < 0:
        raise HandPacketError("sequence must be a non-negative integer")
    if not isinstance(sender_timestamp_ns, int) or sender_timestamp_ns <= 0:
        raise HandPacketError("sender_timestamp_ns must be a positive integer")
    reference_space = value.get("reference_space")
    if reference_space != "local":
        raise HandPacketError("reference_space must be 'local'")
    raw_hands = value.get("hands")
    if not isinstance(raw_hands, Mapping):
        raise HandPacketError("hands must be an object")
    hands = {side: _hand(raw_hands.get(side), side) for side in HAND_SIDES}
    return HandFrame(
        sequence=sequence,
        source_sequence=source_sequence,
        sender_timestamp_ns=sender_timestamp_ns,
        received_monotonic_ns=received_monotonic_ns,
        reference_space=reference_space,
        hands=hands,
        head=_head(value.get("head")),
    )


__all__ = ["PACKET_TYPE", "HandPacketError", "parse_hand_frame"]
