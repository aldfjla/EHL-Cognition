"""Parametric involute spur gear, modelled with CadQuery.

Run it to regenerate the exports next to this file:

    pip install cadquery
    python cad/spur_gear.py

Writes ``spur_gear.step`` (CAD interchange), ``spur_gear.stl`` (mesh, for
printing or simulation) and ``spur_gear.svg`` (isometric preview). Every
dimension lives in the parameter block below; nothing else needs editing.
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq

# ---- Parameters (mm / degrees) ---------------------------------------------
MODULE = 2.0  # tooth size: pitch diameter = MODULE * TEETH
TEETH = 20
PRESSURE_ANGLE = 20.0
FACE_WIDTH = 10.0  # gear thickness
BORE_DIAMETER = 8.0  # shaft hole, 0 to disable
KEYWAY_WIDTH = 3.0  # 0 to disable
KEYWAY_DEPTH = 1.4  # measured outwards from the bore wall
HUB_DIAMETER = 18.0  # 0 to disable
HUB_HEIGHT = 6.0  # hub protrusion above the +Z face
FLANK_POINTS = 12  # involute resolution per flank

OUT_DIR = Path(__file__).resolve().parent


def _involute(base_radius: float, roll: float) -> tuple[float, float]:
    """Point on the involute of a circle, at roll angle ``roll`` radians."""
    return (
        base_radius * (math.cos(roll) + roll * math.sin(roll)),
        base_radius * (math.sin(roll) - roll * math.cos(roll)),
    )


def _rotate(point: tuple[float, float], angle: float) -> tuple[float, float]:
    x, y = point
    c, s = math.cos(angle), math.sin(angle)
    return (x * c - y * s, x * s + y * c)


def gear_profile(
    module: float = MODULE,
    teeth: int = TEETH,
    pressure_angle: float = PRESSURE_ANGLE,
    flank_points: int = FLANK_POINTS,
) -> list[tuple[float, float]]:
    """Closed polyline of the full gear cross-section, centred on the origin.

    Each tooth is a true involute flank pair: the rising flank is sampled from
    the base (or root) circle out to the tip circle, and the falling flank is
    its mirror image about the tooth centreline.
    """
    alpha = math.radians(pressure_angle)
    pitch_radius = module * teeth / 2.0
    base_radius = pitch_radius * math.cos(alpha)
    tip_radius = pitch_radius + module  # 1.0 * module addendum
    root_radius = max(pitch_radius - 1.25 * module, 0.1 * module)

    def roll_at(radius: float) -> float:
        return math.sqrt(max((radius / base_radius) ** 2 - 1.0, 0.0))

    roll_start = roll_at(max(base_radius, root_radius))
    roll_end = roll_at(tip_radius)
    flank = [
        _involute(
            base_radius, roll_start + (roll_end - roll_start) * i / (flank_points - 1)
        )
        for i in range(flank_points)
    ]

    # Tooth half-thickness, expressed as an angle at the base circle: half the
    # circular tooth thickness at the pitch circle, plus the involute function.
    half_angle = math.pi / (2 * teeth) + math.tan(alpha) - alpha
    pitch_angle = 2 * math.pi / teeth
    has_root_land = root_radius < base_radius

    points: list[tuple[float, float]] = []
    for i in range(teeth):
        centre = i * pitch_angle
        rising, falling = centre - half_angle, centre + half_angle
        if has_root_land:
            points.append(_rotate((root_radius, 0.0), rising))
        points.extend(_rotate(p, rising) for p in flank)
        points.extend(_rotate((x, -y), falling) for x, y in reversed(flank))
        if has_root_land:
            points.append(_rotate((root_radius, 0.0), falling))
    return points


def build_gear() -> cq.Workplane:
    gear = cq.Workplane("XY").polyline(gear_profile()).close().extrude(FACE_WIDTH)

    if HUB_DIAMETER > 0 and HUB_HEIGHT > 0:
        gear = gear.faces(">Z").workplane().circle(HUB_DIAMETER / 2).extrude(HUB_HEIGHT)

    if BORE_DIAMETER > 0:
        gear = (
            gear.faces(">Z")
            .workplane(centerOption="CenterOfBoundBox")
            .circle(BORE_DIAMETER / 2)
            .cutThruAll()
        )
        if KEYWAY_WIDTH > 0 and KEYWAY_DEPTH > 0:
            gear = (
                gear.faces(">Z")
                .workplane(centerOption="CenterOfBoundBox")
                .center(0, BORE_DIAMETER / 2 + KEYWAY_DEPTH / 2)
                .rect(KEYWAY_WIDTH, KEYWAY_DEPTH)
                .cutThruAll()
            )
    return gear


def export(gear: cq.Workplane, out_dir: Path = OUT_DIR) -> None:
    cq.exporters.export(gear, str(out_dir / "spur_gear.step"))
    cq.exporters.export(
        gear, str(out_dir / "spur_gear.stl"), tolerance=0.01, angularTolerance=0.1
    )
    cq.exporters.export(
        gear,
        str(out_dir / "spur_gear.svg"),
        opt={
            "width": 900,
            "height": 700,
            "marginLeft": 40,
            "marginTop": 40,
            "showAxes": False,
            "projectionDir": (0.6, 0.5, 0.6),
            "strokeWidth": 0.3,
            "showHidden": False,
        },
    )


def main() -> None:
    gear = build_gear()
    export(gear)
    solid = gear.val()
    bbox = solid.BoundingBox()
    print(f"pitch diameter : {MODULE * TEETH:.2f} mm")
    print(f"tip diameter   : {MODULE * (TEETH + 2):.2f} mm")
    print(f"bounding box   : {bbox.xlen:.2f} x {bbox.ylen:.2f} x {bbox.zlen:.2f} mm")
    print(f"volume         : {solid.Volume() / 1000:.2f} cm^3")


if __name__ == "__main__":
    main()
