from __future__ import annotations

import pytest

from quest3_teleop.model import JOINT_NAMES
from quest3_teleop.protocol import HandPacketError, parse_hand_frame


def packet(*, left: bool = True, right: bool = True) -> dict[str, object]:
    def hand(tracked: bool, offset: float) -> dict[str, object]:
        if not tracked:
            return {"tracked": False}
        positions = []
        rotations = []
        for index in range(len(JOINT_NAMES)):
            positions.extend((offset + index * 0.001, 1.0 + index * 0.002, -0.4 - index * 0.001))
            rotations.extend((0.0, 0.0, 0.0, 1.0))
        return {
            "tracked": True,
            "positions_m": positions,
            "rotations_xyzw": rotations,
            "radii_m": [0.008] * 25,
        }

    return {
        "type": "quest3.hand_frame.v1",
        "sequence": 7,
        "sender_timestamp_ns": 123456789,
        "reference_space": "local",
        "head": {"position_m": [0.0, 1.6, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        "hands": {"left": hand(left, -0.1), "right": hand(right, 0.1)},
    }


def test_complete_bimanual_packet() -> None:
    frame = parse_hand_frame(packet(), sequence=1, received_monotonic_ns=999)
    assert frame.sequence == 1
    assert frame.source_sequence == 7
    assert frame.both_hands_tracked
    assert tuple(frame.hand("left").joints) == JOINT_NAMES
    assert frame.received_monotonic_ns == 999


def test_partial_tracking_is_valid_but_not_bimanual() -> None:
    frame = parse_hand_frame(packet(right=False), sequence=2, received_monotonic_ns=1000)
    assert not frame.both_hands_tracked
    assert not frame.hand("right").tracked


def test_bad_shape_is_rejected() -> None:
    value = packet()
    value["hands"]["left"]["positions_m"] = [0.0]  # type: ignore[index]
    with pytest.raises(HandPacketError):
        parse_hand_frame(value, sequence=1, received_monotonic_ns=1)
