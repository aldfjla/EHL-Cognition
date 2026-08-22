"""Shared fixtures: a tiny arm and a harness, so tests are seconds not minutes."""

from __future__ import annotations

from pathlib import Path

import pytest
from simkit.models import menagerie

#: A two-joint arm over a table-height base. Small enough to compile and step
#: in milliseconds, real enough to exercise the whole runner path.
TOY_ARM_XML = """
<mujoco model="toy_arm">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.002" integrator="implicitfast"/>
  <default>
    <joint damping="1"/>
    <geom rgba="0.7 0.7 0.75 1"/>
  </default>
  <worldbody>
    <body name="base" pos="0 0 0.2">
      <geom type="capsule" fromto="0 0 -0.2 0 0 0" size="0.05" mass="2"/>
      <body name="upper" pos="0 0 0">
        <joint name="shoulder" type="hinge" axis="0 1 0" range="-2.5 2.5"/>
        <geom type="capsule" fromto="0 0 0 0.3 0 0" size="0.04" mass="1.5"/>
        <body name="lower" pos="0.3 0 0">
          <joint name="elbow" type="hinge" axis="0 1 0" range="-2.5 2.5"/>
          <geom type="capsule" fromto="0 0 0 0.25 0 0" size="0.035" mass="1"/>
          <body name="hand" pos="0.25 0 0">
            <geom type="sphere" size="0.03" mass="0.2"/>
            <site name="grip" pos="0 0 0"/>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position name="shoulder_act" joint="shoulder" kp="80" ctrlrange="-2.5 2.5"/>
    <position name="elbow_act" joint="elbow" kp="60" ctrlrange="-2.5 2.5"/>
  </actuator>
  <keyframe>
    <key name="home" qpos="-0.6 0.9"/>
  </keyframe>
</mujoco>
"""

#: Drives the toy arm through a fixed sweep. Deterministic by construction.
SWEEP_HARNESS = '''
"""Toy harness: sweep both joints, then hold."""

import math


def run_episode(model, data, params):
    step = params["step"]
    rate = params["rate_hz"]
    amplitude = float(params.get("object_mass_kg", 0.4))
    for i in range(int(2 * rate)):
        target = amplitude * math.sin(i / rate)
        data.ctrl[0] = -0.6 + target
        data.ctrl[1] = 0.9 - target
        step()
    return {"reached": True}
'''


@pytest.fixture(scope="session")
def toy_arm(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("toy_arm") / "toy_arm.xml"
    path.write_text(TOY_ARM_XML)
    return path


@pytest.fixture(scope="session")
def sweep_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("harness") / "sweep_harness.py"
    path.write_text(SWEEP_HARNESS)
    return path


@pytest.fixture(scope="session")
def task() -> dict:
    return {
        "name": "pick_and_place",
        "rate_hz": 50,
        "success": [
            {"id": "object_in_bin"},
            {"id": "no_collision"},
            {"id": "within_time", "limit_s": 2.0},
            {"id": "joint_limits_respected"},
        ],
    }


@pytest.fixture(scope="session")
def menagerie_dir() -> Path:
    """The real Menagerie checkout, or skip: index tests need real files."""
    directory = menagerie.default_dir()
    if not (directory / "franka_emika_panda").is_dir():
        pytest.skip(f"no Menagerie checkout at {directory}; run `make menagerie`")
    return directory
