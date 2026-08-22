# Pitch

## One line

**Robot CI: push robot code, and an autonomous engineering team simulates it,
finds what breaks, fixes it, and opens a pull request with video proof.**

## The observation

Devin works because software engineering has a closed loop: write code, run
tests, read the failure, fix, re-run. Every step is programmable and the verdict
is machine-checkable. Point an agent at that loop and it can work unsupervised.

Ask what other domain has that loop, and the answer most people miss is
robotics — because the loop is there, it just has a different name.

**Simulation is robotics' test suite.** It is deterministic, it is fast, it
produces a machine-checkable verdict, and MuJoCo has been free and excellent for
years. Every serious robotics lab already has it.

And yet no robotics team has CI. They write control code, flash it to a machine,
try it once by hand, and ship. Regressions are discovered weeks later on
physical hardware — where they cost money, schedule, and occasionally fingers.

The gap is not the simulator. The gap is that **nobody wired an autonomous
engineer into it.**

## What we built

A developer pushes control code for a real robot. With no human in the loop:

1. The system resolves the robot's physical model — from the MuJoCo Menagerie
   library, or by having an agent synthesize one from the repo's kinematics.
2. An agent binds the pushed code, unmodified, into a simulator.
3. The code runs across 24 randomized worlds: payload, friction, sensor noise,
   actuation latency.
4. Failures are clustered by root cause, and a fleet of Devin sessions is
   dispatched — one debugging engineer per cluster, in parallel.
5. Each reproduces its failure from a seed, explains the mechanism, hands off to
   a fixer, and the fixer patches and self-verifies.
6. A tech lead re-runs the **full** suite against all patches merged, because
   two fixes that pass separately can fail together.
7. Output: a pull request with an incident report, the diff, and before/after
   video of the same simulated world.

## Why it is not a demo of agents writing code

**Everything an agent claims is checked by a simulator that has no idea agents
exist.** `simkit` is deterministic, imports nothing from the agent layer, and
runs standalone from a CLI. An agent that says "fixed" has fixed nothing until
the same seeds that were red come back green.

That is the difference between an autonomous system and a plausible one. It also
means the interesting failure mode — an agent confidently wrong — is structurally
caught rather than hoped against.

## The part we did not expect

Devin sessions cannot talk to each other. No channel, no shared memory, no way
to address a peer.

We could have hidden that. Instead it became the architecture: every agent
writes findings to a shared blackboard, and the orchestrator relays them into
other sessions' prompts. Each relay is a typed message, rendered live as a team
chat and a communication graph.

So the "team chat" on the dashboard is not a visualization of collaboration. It
**is** the collaboration — each line is one agent's conclusion being written
into another agent's context. Watching it is watching the coordination layer
that makes seven isolated sessions into an engineering team.

That is the honest architecture and the best part of the demo, which is a rare
thing to get to say.

## Why this is a real product, not a hackathon toy

- **The customer's repo is untouched.** Robot CI is an external system watching
  a repo, like any CI provider. Adoption is a webhook and an optional
  `robotci.yaml`.
- **Library-first models.** It works on day one for any robot in the Menagerie —
  Franka, UR, Kinova, ANYmal, Shadow Hand. Generation is the fallback, not the
  premise.
- **The output is what a team already consumes**: a pull request, a root-cause
  writeup, and a video. No new tool to adopt.
- **It fails honestly.** `FAILED_UNRESOLVED` is a first-class outcome with a
  finite iteration budget. A system that always reports success is a system
  whose success means nothing.

## What we would say to a skeptic

> *"Simulation isn't reality."*

Correct, and the system is built around that rather than around denying it.
Curated vendor models are preferred; generated models are marked low-confidence
with every assumption recorded, and the report says so. A failure against a
generated model is reported as a lead, not a verdict.

But the bug class this catches does not depend on physical fidelity. A
controller that closes its gripper on a fixed 2-second timer is wrong on *every*
robot in *every* simulator, because it assumes an approach duration it never
measures. That is a logic bug, it is the kind currently found on hardware weeks
later, and it is exactly what a randomized simulated matrix surfaces in ninety
seconds.

## The track question, answered

The brief was *Devin for X — build the autonomous layer*, for a domain whose
output can be expressed as code.

Robotics is that domain, and it already had the verifier. We built the layer
that uses it: programmatic Devin sessions, a way for them to check their own
work, real artifacts, no human in the loop.
