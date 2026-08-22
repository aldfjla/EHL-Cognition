"""Robot model sourcing: repository evidence first, generation as fallback.

Resolution order, cheapest and most trustworthy first:

1. ``robotci.yaml`` names a Menagerie model  -> use it.
2. ``robotci.yaml`` names an in-repo MJCF    -> use it.
3. A compiling, actuated in-repo MJCF        -> use it.
4. A repo URDF/xacro converted to MJCF       -> use it.
5. Automatic identification against Menagerie -> use it.
6. Nothing matched -> the Modeler agent synthesizes MJCF from the repo's
   kinematics, and the result is validated by loading it in MuJoCo.

Steps 1-5 cost no agent time. Shipped or converted repository models outrank
kinematic guesses, while dependency and prose name matches are explicitly
approximate. Step 6 is the interesting demo but the worse engineering outcome,
so it is genuinely last.
"""
