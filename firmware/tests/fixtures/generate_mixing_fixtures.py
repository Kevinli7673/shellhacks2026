"""Regenerates mixing_fixtures.json from the exact equations in
IMPLEMENTATION_PLAN.md section 4. Run this after any accepted change to the
mixing algorithm so the fixture stays derived from a single source rather
than hand-edited. It is not part of the firmware build.

    python firmware/tests/fixtures/generate_mixing_fixtures.py
"""
import json
import math
import os

CASES = [
    ("zero_limit", 1.0, 1.0, 1.0, 0,
     "Explicit zero-limit handling: four zero outputs."),
    ("forward_positive", 1.0, 0.0, 0.0, 100,
     "Logical-direction test: positive forward -> all four outputs positive."),
    ("forward_negative", -1.0, 0.0, 0.0, 100,
     "Negative forward reverses all four outputs."),
    ("sideways_positive", 0.0, 1.0, 0.0, 100,
     "Logical-direction test: positive sideways -> FL/RR positive, FR/RL negative."),
    ("sideways_negative", 0.0, -1.0, 0.0, 100,
     "Negative sideways reverses the corresponding outputs."),
    ("turn_positive", 0.0, 0.0, 1.0, 100,
     "Logical-direction test: positive turn -> FL/RL positive, FR/RR negative."),
    ("turn_negative", 0.0, 0.0, -1.0, 100,
     "Negative turn reverses the corresponding outputs."),
    ("diagonal_forward_right", 1.0, 1.0, 0.0, 100,
     "Forward/sideways magnitude exceeds 1; normalized before scaling, then saturation-scaled together."),
    ("diagonal_forward_left", 1.0, -1.0, 0.0, 100,
     "Mirror of diagonal_forward_right."),
    ("forward_plus_turn_saturates", 1.0, 0.0, 1.0, 100,
     "Raw outputs (200, 0, 200, 0) exceed the limit; all four scaled together by 0.5."),
    ("all_axes_combined", 0.6, 0.6, 0.6, 100,
     "Combined translation and rotation; raw max magnitude (180) triggers proportional scaling before rounding."),
    ("small_speed_limit", 1.0, 0.0, 0.0, 1,
     "Common wheel-output normalization boundary at the smallest nonzero limit."),
    ("full_pwm_ceiling", 1.0, 0.0, 0.0, 255,
     "Full protocol PWM ceiling boundary."),
    ("negative_turn_only_half", 0.0, 0.0, -0.5, 200,
     "Fractional turn input, no normalization or saturation triggered."),
]


def round_half_away_from_zero(value):
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def mix(forward, sideways, turn, limit):
    if limit <= 0:
        return (0, 0, 0, 0)

    magnitude = math.sqrt(forward ** 2 + sideways ** 2)
    if magnitude > 1.0:
        forward /= magnitude
        sideways /= magnitude

    f = forward * limit
    s = sideways * limit
    t = turn * limit

    fl = f + s + t
    fr = f - s - t
    rl = f - s + t
    rr = f + s - t

    max_abs = max(abs(fl), abs(fr), abs(rl), abs(rr))
    if max_abs > limit:
        scale = limit / max_abs
        fl *= scale
        fr *= scale
        rl *= scale
        rr *= scale

    outputs = [round_half_away_from_zero(v) for v in (fl, fr, rl, rr)]
    return tuple(max(-limit, min(limit, v)) for v in outputs)


def main():
    fixtures = []
    for name, forward, sideways, turn, limit, note in CASES:
        fl, fr, rl, rr = mix(forward, sideways, turn, limit)
        fixtures.append({
            "name": name,
            "forward": forward,
            "sideways": sideways,
            "turn": turn,
            "speed_limit": limit,
            "expected": {
                "front_left": fl,
                "front_right": fr,
                "rear_left": rl,
                "rear_right": rr,
            },
            "note": note,
        })

    document = {
        "_comment": (
            "Shared mecanum mixing behavioral fixtures. Values computed from "
            "the exact equations in IMPLEMENTATION_PLAN.md section 4 via "
            "tests/fixtures/generate_mixing_fixtures.py and cross-checked by "
            "hand. firmware/tests/test_mixing/test_mixing.cpp asserts these "
            "same values against mix(). The dashboard/control workstream "
            "should reproduce identical values from its Python mock mixing "
            "implementation to keep parity (IMPLEMENTATION_PLAN.md section 4: "
            "'Use shared fixtures to verify Python/C++ output parity')."
        ),
        "fixtures": fixtures,
    }

    out_path = os.path.join(os.path.dirname(__file__), "mixing_fixtures.json")
    with open(out_path, "w") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
    print(f"Wrote {len(fixtures)} fixtures to {out_path}")


if __name__ == "__main__":
    main()
