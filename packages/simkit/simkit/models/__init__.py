"""Robot model sourcing: library first, generation as fallback.

Resolution order, cheapest and most trustworthy first:

1. ``robotci.yaml`` names a Menagerie model  -> use it.
2. ``robotci.yaml`` names an in-repo MJCF    -> use it.
3. Automatic identification against the local Menagerie index -> use it.
4. Nothing matched -> the Modeler agent synthesizes MJCF from the repo's
   kinematics, and the result is validated by loading it in MuJoCo.

Steps 1-3 cost no agent time and produce a physically curated model. Step 4 is
the interesting demo but the worse engineering outcome, so it is genuinely last.
"""
