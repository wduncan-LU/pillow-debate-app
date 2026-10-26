# Pillow debate app

A Streamlit companion to the pillow debate assignment concept for a clinical biomechanics course (see `../Pillow_Assignment_Pitch.md`). Students enter simple home measurements and the app does the calculations, so the class time goes on interpreting the results.

This is a prototype for discussion. It has not been used with students, and its defaults (head-mass fraction, shoulder sink, head and neck widths) are starting values to replace with measurements.

## Pages

| Page | Input | Output |
| :--- | :--- | :--- |
| 1. Your team | Team 1 to 6 | The material and side, both debate briefs, and that material's example data. Pages 2 to 6 then show notes for the material and a prompt for the side |
| 2. Compression and stiffness | Load (kg) and loft (mm) steps | Force-deflection curve, stiffness per region, densification onset, optional overlay of all three example curves |
| 3. Creep | Loft readings over time under one load | Deflection-time curve, creep in mm and % |
| 4. Contact pressure | Body mass, imprint length and width | Average pressure in kPa and mmHg, beside the Landis (1930) 32 mmHg figure |
| 5. Neck angle | Posterior photo, three clicked markers (tragus, C7, upper acromion) | Angle between neck and shoulder lines, lateral flexion from level |
| 6. Loft fit | Sleeper dimensions and mattress type | Loft needed, and the tested pillow's loft under that head |
| 7. Export results | Everything above | ZIP with CSV tables and `summary.json` (including team, material and side) |

The claim audit stays in the instructor outline as a paper exercise, so it isn't in the app.

Teams 1 and 2 are feather and down, 3 and 4 memory foam, and 5 and 6 perforated elastomer. Odd numbers argue FOR and even numbers AGAINST. Each material's example data is made up to show a typical curve shape. Against the default sleeper (needs 9.5 cm), the feather example sits at 6.8 cm, memory foam at 7.0 cm and latex at 10.6 cm, so no example comes out as the obvious winner.

## Calculations

All calculations live in `calcs.py`, which has no Streamlit code.

- Force is load × 9.81. Deflection is unloaded loft minus loaded loft.
- Stiffness for each step is Δforce / Δdeflection. The softest step marks the plateau. Earlier steps at least 2× as stiff are the initial region, and the first later step at least 3× as stiff starts densification. Students can change both multiples. Region stiffness is the secant across the region.
- Head force is body mass × 0.081 (head and neck, Winter 2009) × 9.81, unless the student enters their own.
- Contact area is an ellipse, π/4 × length × width. 1 kPa = 7.50062 mmHg.
- Neck angle is the angle at C7 between C7→tragus and C7→upper acromion. Level is 90°. Above 90° means the head has dropped toward the mattress.
- Needed loft = acromion-to-neck distance − shoulder sink + neck width/2 − head width/2.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/streamlit run app.py
.venv/bin/python -m pytest -q tests
```

The tests cover the calculations and walk every page with Streamlit's AppTest, including the example data flowing into the later pages and values surviving page changes. AppTest can't drive the photo-click component. It was checked by hand in a browser on 26 October 2026: three clicks on a test image gave the expected 91.0°.

Nothing is stored on a server. Photos stay in memory for the session and are not included in the export.
