# Demo script — 90 seconds

The demo must show three things, in this order, and nothing else:

1. A human pushes ordinary robot code and walks away.
2. A team of agents forms, finds a real bug, and argues about it visibly.
3. The output is a pull request with working code and video proof.

## Setup (before you present)

- [ ] `make setup` clean, `make menagerie` complete
- [ ] `.env` filled: `DEVIN_API_KEY`, `GITHUB_TOKEN`, `TARGET_REPO`
- [ ] `scripts/devin_smoke.py` passes **on the venue wifi**
- [ ] Demo repo pushed and known-broken: a pick-and-place controller with a
      hardcoded `GRIP_TIMEOUT = 2.0`, which fails whenever a low-friction or
      heavy-payload approach takes longer than that
- [ ] One full run completed **that morning**, its artifacts on disk
- [ ] Browser replay at `/runs/run_replay_demo` verified working as the fallback
- [ ] Browser: terminal + dashboard side by side, dashboard zoomed for the room
- [ ] Menagerie model cached — no downloading on stage

## The 90 seconds

**0:00 – 0:12 · The problem** *(terminal visible, no dashboard yet)*

> "This is control code for a robot arm. It works on my desk. Nobody knows if
> it works anywhere else, because robotics has no CI — you test on hardware, by
> hand, once."

Type the push. Do not narrate the command.

```bash
git push origin main
```

**0:12 – 0:25 · The team forms** *(switch to dashboard)*

The run appears. Stages tick: `RESOLVE_MODEL` → `BUILD_HARNESS`.

> "Our system woke up on that push. It figured out which robot this code drives,
> pulled the real physical model from MuJoCo Menagerie, and had an agent wire
> the pushed code into a simulator — unmodified."

Point at the two agent cards. Do not read them aloud.

**0:25 – 0:40 · The matrix** *(ScenarioMatrix filling)*

24 grey cells appear, then fill in green — then red.

> "Now it's running that code across 24 randomized worlds. Different payloads,
> different friction, different sensor noise. This is what a robotics team never
> does, because doing it by hand takes a week."

Let the red cells land. **Stop talking while the grid fills.** The visual does
the work.

**0:40 – 0:58 · The fan-out** *(AgentGrid + TeamChat)*

Five red cells cluster into two groups. Two new agent cards appear.

> "Five failures, two distinct causes. It grouped them, and dispatched a
> debugging engineer to each — in parallel."

Point at TeamChat as messages arrive.

> "And this is the part I'd watch. These agents can't talk to each other —
> Devin sessions are isolated. So the orchestrator relays their findings. What
> you're reading isn't a visualization of collaboration. It *is* the
> collaboration — that message is one agent's conclusion being written into
> another agent's context."

**0:58 – 1:12 · The fix and the gate** *(VERIFY stage)*

> "Each engineer patched its own bug and checked its own scenarios. Then a tech
> lead re-ran the *entire* suite against both patches merged — because two fixes
> that pass separately can fail together, and that's the state that ships."

Grid goes green.

**1:12 – 1:30 · The proof** *(VideoCompare, then the PR)*

Play the before/after pair.

> "Same seed. Same world. Same physics. Left is before, right is after. The only
> difference is the patch."

Open the PR on GitHub.

> "Real pull request. Root cause, the diff, and the video. No human touched
> this. Robotics teams already had the feedback loop — it's called simulation.
> Nobody had put an engineer inside it."

Stop.

## Rules for the run

**Do not narrate the UI.** If you find yourself reading a card aloud, the card
is not legible enough — fix the card, not the script.

**Let the grid fill in silence.** The strongest twelve seconds of the demo have
no voiceover.

**Never say "as you can see".**

**Fifteen seconds on the constraint.** The orchestrator-mediated relay is the
most technically interesting thing here, and it is the part a judge cannot get
from the screenshot. It gets its own beat at 0:40.

## Fallbacks

Decide the fallback *before* you start, not mid-demo.

| Failure | Response |
|---|---|
| Devin API slow/down | Cut to the morning's completed run. Say "this ran an hour ago" — never imply it's live. |
| A session hangs > 20s | Keep talking through the fan-out explanation; the pipeline is bounded and will move. |
| Venue wifi dies | Open `/runs/run_replay_demo` in the browser. Say "this is a replay" out loud. |
| Suite passes clean (no bug!) | You pushed the wrong commit. Have the broken SHA in your paste buffer. |
| Dashboard blank | Reload — `?since=0` replays the buffer. Do not debug on stage. |

**The one rule:** if something is pre-recorded or replayed, say so in the same
breath. A judge who catches you presenting a replay as live has stopped
believing everything else too, and the honest version of this demo is strong
enough not to need it.

## The question you will be asked

> *"How do you know the simulation matches the real robot?"*

The honest answer, which is also the better one:

> "We don't, exactly — and the system says so. Library models from Menagerie are
> the calibrated ones vendors publish, and we prefer them. When we have to
> generate a model, we record every assumption, mark confidence low, and the
> report states it. So a failure against a generated model is a lead, not a
> verdict. What sim-to-real gives you reliably is the class of bug that's
> *logically* wrong — a fixed timer that assumes constant approach speed is
> wrong on any robot, in any simulator. That's the bug we're catching, and
> that's the bug that's currently found on hardware."
