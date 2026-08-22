# CAD

Parametric part models, scripted so a diff of the source is a diff of the part.

## `spur_gear.py`

A 20-tooth involute spur gear with a keyed bore and a hub.

| | |
|---|---|
| module | 2 mm |
| teeth | 20 |
| pressure angle | 20° |
| pitch / tip diameter | 40 mm / 44 mm |
| face width | 10 mm |
| bore | Ø8 mm, 3 × 1.4 mm keyway |
| hub | Ø18 mm, 6 mm proud of the face |

Exports checked in alongside the script:

- `spur_gear.step` — CAD interchange (import into FreeCAD, Fusion, SolidWorks)
- `spur_gear.stl` — mesh, for printing or dropping into a sim scene
- `spur_gear.svg` — isometric preview

Regenerate after changing any parameter at the top of the script:

```bash
pip install cadquery
python cad/spur_gear.py
```

The tooth flanks are true involutes rather than an approximation, so the gear
meshes correctly with any other 20°/module-2 gear generated from this script —
change `TEETH` only and the pair will run at the right centre distance
(`MODULE * (z1 + z2) / 2`).
