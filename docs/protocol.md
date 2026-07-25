# Hand-frame protocol

`quest3-teleop` uses one JSON packet type:

```text
quest3.hand_frame.v1
```

Changing the meaning, units, ordering, or required shape of an existing field
requires a new packet type. Additive metadata may be introduced only when old
receivers can safely ignore it.

## Packet

```json
{
  "type": "quest3.hand_frame.v1",
  "sequence": 42,
  "sender_timestamp_ns": 123456789,
  "reference_space": "local",
  "head": {
    "position_m": [0.0, 1.6, 0.0],
    "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]
  },
  "hands": {
    "left": {
      "tracked": true,
      "positions_m": [],
      "rotations_xyzw": [],
      "radii_m": []
    },
    "right": {
      "tracked": false
    }
  }
}
```

Fields:

- `sequence`: browser-local non-negative frame counter. It may restart after a
  Browser reload.
- `sender_timestamp_ns`: WebXR frame time converted to nanoseconds. It is useful
  for ordering within the source but is not synchronized with desktop clocks.
- `reference_space`: always WebXR `local`.
- `head`: optional WebXR viewer pose in the same reference space.
- `hands`: always contains `left` and `right`.
- `tracked: false`: a valid observation in which the complete hand skeleton was
  unavailable.
- `positions_m`: 25 XYZ vectors flattened to 75 numbers, in metres.
- `rotations_xyzw`: 25 quaternions flattened to 100 numbers.
- `radii_m`: 25 WebXR joint-radius estimates, in metres.

The joint order is:

```text
wrist
thumb_metacarpal thumb_proximal thumb_distal thumb_tip
index_metacarpal index_proximal index_intermediate index_distal index_tip
middle_metacarpal middle_proximal middle_intermediate middle_distal middle_tip
ring_metacarpal ring_proximal ring_intermediate ring_distal ring_tip
little_metacarpal little_proximal little_intermediate little_distal little_tip
```

These names are exposed as `quest3_teleop.JOINT_NAMES`.

## Desktop normalization

The desktop validates lengths, finite values, quaternion norms, radii, packet
type, and reference space before publishing a frame.

`HandFrame` adds:

- `sequence`: a desktop-assigned monotonic counter that survives Browser page
  reloads;
- `source_sequence`: the original browser counter;
- `received_monotonic_ns`: the desktop receive time.

Consumers must use `received_monotonic_ns` or `Quest3Receiver.age_s()` for
freshness and timeout decisions. They must not compare `sender_timestamp_ns`
against the desktop wall clock.

A packet with one or both untracked hands is valid and increments `partial`.
Malformed packets are dropped and increment `malformed`; they never replace
the latest valid `HandFrame`.

## Robot boundary

Positions and orientations remain raw WebXR measurements in the Quest `local`
frame. This protocol does not define:

- a robot base frame;
- palm synthesis;
- filtering;
- calibration;
- retargeting;
- safety behavior;
- motor commands.

Those transformations belong to the downstream robot plugin or repository.
