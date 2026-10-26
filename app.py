from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image, ImageDraw, ImageOps

import calcs

st.set_page_config(page_title="Pillow Debate | Biomechanics Lab", layout="wide")

PAGES = [
    ("Start here", "What the assignment asks and how this app helps"),
    ("1. Your team", "Pick your team, material and side"),
    ("2. Compression and stiffness", "A virtual load test, step by step"),
    ("3. Creep", "Deflection over time under a constant load"),
    ("4. Contact pressure", "Head force over contact area"),
    ("5. Neck angle", "Lateral flexion from a side-lying photo"),
    ("6. Loft fit", "Does this pillow suit this sleeper?"),
    ("7. Export results", "Download everything for your submission"),
]
REGION_COLOURS = {"initial": "#3f5d8a", "plateau": "#116b68", "densification": "#d36b39"}
MATERIAL_COLOURS = {"feather": "#8c4c63", "memory_foam": "#116b68", "elastomer": "#71883f"}
MARKERS = ("Tragus", "C7", "Acromion (upper shoulder)")
MARKER_COLOURS = ("#d36b39", "#116b68", "#3f5d8a")
SHOULDER_POINTS = [(200.0, 200.0), (195.0, 70.0)]  # C7 and upper acromion for the example photos

# Each material's brief comes from the instructor outline. Example data is made up to show typical curve
# shapes, not measured, and each example pillow misfits the default sleeper in its own way.
MATERIALS = {
    "feather": {
        "name": "Feather and down",
        "teams": (1, 2),
        "for": "Fill shifts and reshapes under the head, so the pillow conforms to any posture and resists head "
               "rotation very little.",
        "against": "The fill compresses and stays compressed. Loft drops during the night and over months of use, "
                   "and there is no elastic recovery. In Gordon et al. (2010), feather pillows produced the most "
                   "waking symptoms and the most dropouts.",
        "look_for": {
            "compression": "Loose fill has no cell walls to buckle, so expect steady stiffening with no clear "
                           "plateau. Much of the early deflection is fill moving sideways rather than compressing. "
                           "If the app reports densification very early, that is why.",
            "creep": "Fill settles and stays settled. After the last reading, take the load off and measure "
                     "whether the loft comes back.",
            "pressure": "Fill flows around the head, so the imprint tends to be large and irregular. Trace it rather "
                        "than guessing.",
        },
        "example": {"unloaded": 140.0, "loft": [110, 95, 85, 78, 68, 62, 58, 55, 51, 48],
                    "creep": [68, 67, 66.5, 66, 65.5], "contact": (16.0, 12.0), "tragus": (329.3, 213.1)},
    },
    "memory_foam": {
        "name": "Memory foam",
        "teams": (3, 4),
        "for": "Slow recovery spreads the head's load over a large contact area and lowers peak pressure.",
        "against": "Stiffness depends on temperature, so the pillow is firmer in a cold room. Foam creeps under a "
                   "sustained load and can bottom out, and it holds heat. In Gordon et al. (2010), a contoured foam "
                   "pillow did no better than a regular foam one.",
        "look_for": {
            "compression": "Expect the three regions: a short stiff start, a long soft plateau while the cell walls "
                           "buckle, then densification once the cells have collapsed.",
            "creep": "Viscoelastic foam keeps sinking for minutes under the same load. If you can, repeat the test "
                     "in a cooler room and compare.",
            "pressure": "Slow recovery lets the foam mould to the head, which usually gives a large imprint.",
        },
        "example": {"unloaded": 120.0, "loft": [116, 108, 98, 88, 70, 60, 55, 52, 48, 45],
                    "creep": [70, 67, 65.5, 64, 63], "contact": (14.0, 10.0), "tragus": (329.9, 205.0)},
    },
    "elastomer": {
        "name": "Perforated elastomer (pin-core latex, perforated gel foam)",
        "teams": (5, 6),
        "for": "The material springs back quickly, so loft holds through the night. The perforations let air move, "
               "and the gel layer feels cool on first contact. Latex performed best in Gordon et al. (2010).",
        "against": "Stress concentrates around the edges of the perforations, which is where tears start. Gel feels "
                   "cool only until it reaches skin temperature. The material is heavy, and its stiffness still "
                   "changes with room temperature.",
        "look_for": {
            "compression": "Latex and gel foams behave more like a spring: fairly steady stiffness and a quick "
                           "return. Household weights may not be enough to reach densification.",
            "creep": "Expect little creep. If you see a lot, check whether the board is resting over a perforation "
                     "or a torn spot.",
            "pressure": "A springier surface pushes back harder, so the imprint is often smaller. Compare yours with "
                        "the other teams'.",
        },
        "example": {"unloaded": 135.0, "loft": [130, 125, 120, 115, 106, 97, 89, 82, 71, 62],
                    "creep": [106, 105.5, 105.3, 105.2, 105.1], "contact": (13.0, 9.0), "tragus": (329.5, 188.2)},
    },
}
EXAMPLE_LOADS_KG = [1, 2, 3, 4, 6, 8, 10, 12, 16, 20]
EXAMPLE_CREEP_MIN = [0, 1, 2, 5, 10]
EXAMPLE_CREEP_KG = 6.0
TEAMS = {n: (key, "FOR" if n % 2 else "AGAINST") for key, m in MATERIALS.items() for n in m["teams"]}
SIDE_PROMPTS = {
    "FOR": "Your side: find the numbers that support this material. Which result here is your strongest, and "
           "what mechanism explains it?",
    "AGAINST": "Your side: find the weakness. Which result here would matter most to a sleeper, and what "
               "mechanism explains it?",
}

ss = st.session_state
ss.setdefault("results", {})
ss.setdefault("editor_version", 0)
ss.setdefault("points", [])
ss.setdefault("last_click", None)


def blank(columns: list[str], rows: int = 8) -> pd.DataFrame:
    return pd.DataFrame({c: [None] * rows for c in columns}, dtype="float")


def _keep(key: str) -> None:
    ss[key] = ss["w_" + key]


def bind(key: str, default) -> dict:
    """Widget kwargs that keep a value in session state after its page is left.

    Streamlit deletes a widget's own state when the widget isn't drawn, so the value lives under `key`
    and the widget uses a shadow key that is refilled from it on every run.
    """
    ss.setdefault(key, default)
    ss["w_" + key] = ss[key]
    return {"key": "w_" + key, "on_change": _keep, "args": (key,)}


def table_editor(name: str, columns: dict[str, str], rows: int) -> pd.DataFrame:
    """A data editor whose edits survive leaving the page (see bind)."""
    ss.setdefault(name + "_saved", blank(list(columns), rows))
    ekey = f"{name}_editor_{ss.editor_version}"
    if ekey not in ss:
        ss[name + "_base"] = ss[name + "_saved"].copy()
    edited = st.data_editor(
        ss[name + "_base"], num_rows="dynamic", key=ekey, width="stretch",
        column_config={c: st.column_config.NumberColumn(label, min_value=0.0) for c, label in columns.items()},
    )
    ss[name + "_saved"] = edited
    return edited


def team() -> tuple[int, str, str] | None:
    """(team number, material key, side) for the chosen team, or None."""
    n = ss.get("team_no")
    return (n, *TEAMS[n]) if n in TEAMS else None


def short_name(key: str) -> str:
    return MATERIALS[key]["name"].split(" (")[0]


def team_label(n: int) -> str:
    key, side = TEAMS[n]
    return f"Team {n} · {short_name(key)} · {side}"


def _pick_team(side: str) -> None:
    """Choosing in one side's dropdown clears the other, and restarts page 2's virtual test."""
    n = ss["w_team_" + side]
    if n is not None and n != ss.get("team_no"):
        ss.team_no = n
        ss.comp_stage, ss.comp_steps = 0, 0


def team_note(page: str) -> None:
    """Material-specific guidance and the team's debate prompt, shown under each measurement page's title."""
    t = team()
    if t is None:
        st.caption("Pick your team on page 1 to see notes for your material.")
        return
    n, key, side = t
    st.caption(team_label(n))
    look = MATERIALS[key]["look_for"].get(page)
    if look:
        st.info(f"What to look for with {MATERIALS[key]['name'].split(' (')[0].lower()}: {look}")


def side_prompt() -> None:
    t = team()
    if t is not None:
        st.caption(SIDE_PROMPTS[t[2]])


def example_curve(key: str) -> pd.DataFrame:
    return pd.DataFrame({"load_kg": EXAMPLE_LOADS_KG, "loft_mm": MATERIALS[key]["example"]["loft"]}).astype(float)


def load_example(key: str) -> None:
    """Fills pages 3 to 6 with the material's example and marks page 2's virtual test as done. The team's own
    compression table is left alone."""
    ex = MATERIALS[key]["example"]
    ss.comp_source, ss.comp_steps = "virtual", len(EXAMPLE_LOADS_KG)
    ss.creep_unloaded = ex["unloaded"]
    ss.creep_load = EXAMPLE_CREEP_KG
    ss.creep_saved = pd.DataFrame({"time_min": EXAMPLE_CREEP_MIN, "loft_mm": ex["creep"]}).astype(float)
    ss.contact_length, ss.contact_width = ex["contact"]
    ss.points = [ex["tragus"], *SHOULDER_POINTS]
    ss.results = {}
    ss.editor_version += 1


def compression_input() -> tuple[pd.DataFrame, float] | None:
    """(load and loft table, unloaded loft) from whichever source page 2 is set to: the virtual pillow's readings
    so far, or the team's own measurements."""
    if ss.get("comp_source", "virtual") == "virtual":
        t = team()
        if t is None or ss.get("comp_steps", 0) == 0:
            return None
        return example_curve(t[1]).iloc[:ss.comp_steps], MATERIALS[t[1]]["example"]["unloaded"]
    if "compression_saved" not in ss:
        return None
    return ss.compression_saved, float(ss.get("unloaded_loft", 120.0))


def compression_result() -> calcs.CompressionResult | None:
    """Recomputed from saved inputs so later pages don't depend on page 2 having been opened."""
    if (inp := compression_input()) is None:
        return None
    res = calcs.compression_curve(*inp, ss.get("densify_factor", 3.0), ss.get("initial_factor", 2.0))
    return None if res.segments.empty else res


def creep_result():
    if "creep_saved" not in ss:
        return None
    table, summary = calcs.creep(ss.creep_saved, ss.get("creep_unloaded", 120.0))
    return None if summary is None else (table, summary, ss.get("creep_load", 6.0))


def rule_box() -> None:
    st.info("Debate rule: every claim needs a number, a unit, and the mechanism that produces it. "
            "\"Good support\", \"comfortable\", \"alignment\" and \"natural feel\" earn nothing on their own.")


def fmt(value: float | None, unit: str, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f} {unit}"


# ---------- pages ----------

def page_start() -> None:
    st.title("The pillow debate")
    st.write(
        "Sleep-product ads make claims about mechanics: \"zero-gravity support\", \"spinal alignment\", "
        "\"cooling gel\". Over four weeks your team audits one of those claims, measures a pillow, debates "
        "for or against a material, and then each of you writes a specification for one sleeper."
    )
    st.markdown(
        "| Week | What you do | This app |\n|:--|:--|:--|\n"
        "| 1 | Claim audit (group, on paper) | Pick your team on page 1 |\n"
        "| 2 | Measure your pillow and read the literature (group) | Pages 2 to 5 |\n"
        "| 3 | In-class debate (group) | Use your results |\n"
        "| 4 | Specification, decision matrix and oral defense (individual) | Page 6, then export on page 7 |"
    )
    rule_box()
    st.caption("Nothing you enter is saved on a server. If you close the tab, your entries are gone, so export "
               "before you leave.")


def page_team() -> None:
    st.title("Your team")
    st.write("Six teams, three materials. Each material has one team arguing for it and one arguing against it. "
             "Both teams measure the same type of pillow.")
    st.write("Find your team number in one of the two lists. Choosing in one list clears the other.")
    ss.setdefault("team_no", None)
    c = st.columns(2)
    for col, side in zip(c, ("FOR", "AGAINST")):
        options = [n for n in sorted(TEAMS) if TEAMS[n][1] == side]
        key = "team_" + side.lower()
        ss["w_" + key] = ss.team_no if ss.team_no in options else None
        with col:
            st.markdown(f"#### Arguing {side}")
            st.caption("You defend the material." if side == "FOR" else "You attack the material.")
            st.selectbox(f"Teams arguing {side}", options, key="w_" + key, on_change=_pick_team,
                         args=(side.lower(),), format_func=lambda n: f"Team {n} · {short_name(TEAMS[n][0])}",
                         placeholder="Choose your team", label_visibility="collapsed")
    t = team()
    if t is None:
        return
    n, key, side = t
    m = MATERIALS[key]
    st.subheader(m["name"])
    c = st.columns(2)
    for col, s_name in zip(c, ("FOR", "AGAINST")):
        with col:
            mark = " (your side)" if s_name == side else ""
            st.markdown(f"**The {s_name} case{mark}**")
            st.write(m["for" if s_name == "FOR" else "against"])
    st.write(f"Your opponents are Team {m['teams'][1] if side == 'FOR' else m['teams'][0]}. They will measure the "
             "same kind of pillow, so expect them to use the same numbers against you.")
    rule_box()

    st.subheader("Try it with example data")
    st.write(f"Page 2 walks you through a compression test on a virtual {short_name(key).lower()} pillow. The other "
             "pages need your own measurements, or you can fill them with a made-up example to see what they do. "
             "Loading the example overwrites what you've entered on pages 3 to 5.")
    if st.button("Load example data", type="primary"):
        load_example(key)
        st.success("Example data loaded into pages 3 to 6, and page 2's virtual test is filled in.")


COMP_STAGES = ["The pillow at rest", "Add a head", "Add weights in steps", "From readings to numbers",
               "The force-deflection curve"]
HEAD_STEP = EXAMPLE_LOADS_KG.index(6)  # the 6 kg step stands in for a 75 kg adult's head and neck (6.1 kg)

# Drawing scale and layout for the test rig, in SVG user units.
PX_PER_MM = 1.2
RIG_BASE, RIG_X0, RIG_X1, RIG_CX = 320, 100, 420, 260
DIM_X, RULER_X = 448, 548


def _rig_top(loft_mm: float) -> float:
    return RIG_BASE - loft_mm * PX_PER_MM


def pillow_texture(key: str, pid: str, colour: str) -> str:
    if key == "feather":
        return (f'<pattern id="{pid}" width="22" height="14" patternUnits="userSpaceOnUse">'
                f'<path d="M3 10 q5 -7 10 0" fill="none" stroke="{colour}" stroke-opacity=".45"/></pattern>')
    if key == "memory_foam":
        return (f'<pattern id="{pid}" width="8" height="8" patternUnits="userSpaceOnUse">'
                f'<circle cx="4" cy="4" r="1.3" fill="{colour}" fill-opacity=".35"/></pattern>')
    return (f'<pattern id="{pid}" width="22" height="22" patternUnits="userSpaceOnUse">'
            f'<circle cx="11" cy="11" r="4.5" fill="none" stroke="{colour}" stroke-opacity=".6"/></pattern>')


def head_shape(y_bottom: float) -> str:
    """A sleeping face lying on its side (crown to the right), resting on the board at y_bottom."""
    cx, cy = RIG_CX, y_bottom - 44
    face = (
        '<ellipse cx="0" cy="0" rx="42" ry="52" fill="#e9c6a1" stroke="#8a6545" stroke-width="1.5"/>'
        '<path d="M-43 -6 C-46 -62 46 -62 43 -6 C30 -30 -30 -30 -43 -6 z" fill="#5a4130"/>'
        '<ellipse cx="-44" cy="4" rx="6" ry="11" fill="#e0b48c" stroke="#8a6545"/>'
        '<ellipse cx="44" cy="4" rx="6" ry="11" fill="#e0b48c" stroke="#8a6545"/>'
        '<path d="M-22 2 q8 6 16 0 M6 2 q8 6 16 0" fill="none" stroke="#5a4130" stroke-width="2.2" '
        'stroke-linecap="round"/>'
        '<path d="M0 8 q-4 12 2 14" fill="none" stroke="#8a6545" stroke-width="1.6" stroke-linecap="round"/>'
        '<path d="M-9 32 q9 5 18 0" fill="none" stroke="#8a6545" stroke-width="2" stroke-linecap="round"/>'
    )
    return (f'<g transform="translate({cx} {cy}) rotate(90)">{face}</g>'
            f'<text x="{cx + 66}" y="{cy - 10}" class="mid">6 kg</text>'
            f'<text x="{cx + 66}" y="{cy + 10}" class="small">one adult head</text>')


def rig_svg(key: str, unloaded: float, loft_from: float, loft_to: float, blocks: list[float], head: bool,
            animate_last: bool, nonce: int, caption: str) -> str:
    """Side view of the compression test: pillow, board, load, ruler and the loft reading.

    The final state is drawn as the base style, and the animation runs from the previous state to it, so the
    reduced-motion path simply shows the result.
    """
    c = MATERIAL_COLOURS[key]
    u = f"r{nonce}"
    s_from, s_to = loft_from / unloaded, loft_to / unloaded
    y_from, y_to, y_unloaded = _rig_top(loft_from), _rig_top(loft_to), _rig_top(unloaded)
    moving = animate_last and loft_from != loft_to
    h = unloaded * PX_PER_MM
    rx = min(36, h / 2) if key == "feather" else 8

    # Load stack, in coordinates where y=0 is the top of the pillow and the board sits just above it.
    load = [f'<rect x="{RIG_X0 - 12}" y="-7" width="{RIG_X1 - RIG_X0 + 24}" height="7" rx="2" class="board"/>',
            f'<line x1="{RIG_X1 + 14}" y1="0" x2="{RULER_X - 2}" y2="0" class="reading"/>']
    y = -7.0
    for i, kg in enumerate(blocks):
        bh = 6 + 3 * kg
        y -= bh
        cls = ' class="drop"' if animate_last and i == len(blocks) - 1 else ""
        load.append(f'<g{cls}><rect x="{RIG_CX - 78}" y="{y}" width="156" height="{bh - 1.5}" rx="2" '
                    f'fill="{"#4f5d70" if i % 2 else "#6b7a8f"}"/></g>')
    if head:
        load.append(f'<g class="drop">{head_shape(-7)}</g>' if animate_last else head_shape(-7))

    total = sum(blocks) + (6 if head else 0)
    label = (f"Head on the board: 6 kg ({6 * calcs.G:.0f} N)" if head else
             f"Load on the board: {total:g} kg ({total * calcs.G:.0f} N)" if blocks else "Just the board, no weight yet")
    ruler = [f'<line x1="{RULER_X}" y1="{RIG_BASE}" x2="{RULER_X}" y2="{_rig_top(160)}" class="ink"/>']
    for mm in range(0, 161, 10):
        yy = _rig_top(mm)
        ruler.append(f'<line x1="{RULER_X}" y1="{yy}" x2="{RULER_X + (12 if mm % 50 == 0 else 6)}" y2="{yy}" class="ink"/>')
        if mm % 20 == 0:
            ruler.append(f'<text x="{RULER_X + 16}" y="{yy + 4}" class="small">{mm}</text>')
    ruler.append(f'<text x="{RULER_X + 16}" y="{_rig_top(160) - 10}" class="small">mm</text>')

    deflection = ""
    if loft_to < unloaded:
        d = unloaded - loft_to
        deflection = (f'<g class="late"><line x1="{RIG_X0}" y1="{y_unloaded}" x2="{RULER_X}" y2="{y_unloaded}" '
                      f'class="ghost"/><text x="{RULER_X - 8}" y="{y_unloaded - 8}" text-anchor="end" class="small">unloaded '
                      f'{unloaded:.0f} mm</text>'
                      f'<line x1="{RIG_X0 - 22}" y1="{y_unloaded}" x2="{RIG_X0 - 22}" y2="{y_to}" class="accent"/>'
                      f'<text x="{RIG_X0 - 28}" y="{(y_unloaded + y_to) / 2 - 4}" text-anchor="end" class="small">'
                      f'deflection</text><text x="{RIG_X0 - 28}" y="{(y_unloaded + y_to) / 2 + 13}" '
                      f'text-anchor="end" class="mid">{d:.0f} mm</text></g>')
    dim = (f'<g class="late"><line x1="{DIM_X}" y1="{RIG_BASE - 3}" x2="{DIM_X}" y2="{y_to + 3}" class="dim" '
           f'marker-start="url(#a{u})" marker-end="url(#a{u})"/>'
           f'<text x="{DIM_X + 9}" y="{(RIG_BASE + y_to) / 2 - 4}" class="small">loft</text>'
           f'<text x="{DIM_X + 9}" y="{(RIG_BASE + y_to) / 2 + 14}" class="big">{loft_to:.0f} mm</text></g>')

    anim = ""
    if animate_last:
        drop = (f"@keyframes d{u}{{0%{{transform:translateY(-140px);opacity:0}}35%{{transform:none;opacity:1}}}}"
                f".{u} .drop{{animation:d{u} 1.8s ease-in-out}}")
        squash = (f"@keyframes s{u}{{0%,35%{{transform:scaleY({s_from:.4f})}}}}"
                  f"@keyframes l{u}{{0%,35%{{transform:translateY({y_from:.1f}px)}}}}"
                  f".{u} .pillow{{animation:s{u} 1.8s ease-in-out}}.{u} .load{{animation:l{u} 1.8s ease-in-out}}"
                  if moving else "")
        late = f"@keyframes f{u}{{0%,85%{{opacity:0}}}}.{u} .late{{animation:f{u} 2.1s ease-out}}"
        anim = drop + squash + late
    style = (
        f".{u}{{max-width:660px}}.{u} svg{{width:100%;height:auto;display:block;font-family:inherit;"
        f"font-variant-numeric:tabular-nums}}"
        f".{u} text{{fill:currentColor}}.{u} .small{{font-size:15px;opacity:.8}}.{u} .mid{{font-size:17px;font-weight:600}}"
        f".{u} .big{{font-size:21px;font-weight:700}}.{u} .ink{{stroke:currentColor;stroke-opacity:.7}}"
        f".{u} .board{{fill:#8a7a62}}.{u} .reading{{stroke:#d36b39;stroke-width:1.5;stroke-dasharray:5 4}}"
        f".{u} .ghost{{stroke:currentColor;stroke-opacity:.45;stroke-dasharray:2 4}}"
        f".{u} .accent{{stroke:#d36b39;stroke-width:3}}.{u} .dim{{stroke:#d36b39;stroke-width:2}}"
        f".{u} .pillow{{transform-origin:{RIG_CX}px {RIG_BASE}px;transform:scaleY({s_to:.4f})}}"
        f".{u} .load{{transform:translateY({y_to:.1f}px)}}"
        f"{anim}@media (prefers-reduced-motion:reduce){{.{u} *{{animation:none!important}}}}"
    )
    return (
        f'<div class="{u}"><style>{style}</style>'
        f'<svg viewBox="0 0 640 360" role="img" aria-label="{caption}">'
        f'<defs>{pillow_texture(key, "p" + u, c)}'
        f'<marker id="a{u}" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="#d36b39"/></marker></defs>'
        f'<text x="16" y="28" class="mid">{label}</text>'
        f'<rect x="30" y="{RIG_BASE}" width="590" height="12" fill="currentColor" fill-opacity=".12"/>'
        f'<line x1="30" y1="{RIG_BASE}" x2="620" y2="{RIG_BASE}" class="ink"/>'
        f'<g class="pillow"><rect x="{RIG_X0}" y="{y_unloaded}" width="{RIG_X1 - RIG_X0}" height="{h}" rx="{rx}" '
        f'fill="{c}" fill-opacity=".22"/><rect x="{RIG_X0}" y="{y_unloaded}" width="{RIG_X1 - RIG_X0}" height="{h}" '
        f'rx="{rx}" fill="url(#p{u})" stroke="{c}" stroke-width="2"/></g>'
        f'{"".join(ruler)}{deflection}{dim}'
        f'<g class="load">{"".join(load)}</g>'
        f'</svg></div>'
    )


def rig(svg: str) -> None:
    # st.html sanitises SVG away, so the drawing goes through markdown's raw HTML instead.
    st.markdown(svg, unsafe_allow_html=True)


def _go_stage(i: int) -> None:
    ss.comp_stage = i
    ss.anim += 1


def _set_steps(k: int) -> None:
    ss.comp_steps = k
    ss.anim += 1


def stepper(stage: int) -> None:
    items = "".join(
        f'<li class="{"now" if i == stage else "done" if i < stage else ""}">'
        f'<span>{i + 1}</span>{name}</li>' for i, name in enumerate(COMP_STAGES))
    st.html(
        "<style>.stepper{display:flex;flex-wrap:wrap;gap:6px 18px;list-style:none;padding:0;margin:0 0 4px;"
        "font-size:14px}.stepper li{display:flex;align-items:center;gap:6px;opacity:.55}"
        ".stepper li span{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;"
        "border:1.5px solid currentColor;font-size:12px;font-variant-numeric:tabular-nums}"
        ".stepper li.done{opacity:.8}.stepper li.now{opacity:1;font-weight:600}"
        ".stepper li.now span{background:#116b68;border-color:#116b68;color:#fff}</style>"
        f'<ol class="stepper" aria-label="Steps in the compression test">{items}</ol>'
    )


def stage_nav(stage: int, can_continue: bool = True) -> None:
    c = st.columns([1, 1, 3])
    if stage > 0:
        c[0].button("Back", key=f"comp_back_{stage}", on_click=_go_stage, args=(stage - 1,), width="stretch")
    if stage < len(COMP_STAGES) - 1:
        c[1].button("Continue", key=f"comp_next_{stage}", type="primary" if can_continue else "secondary",
                    on_click=_go_stage, args=(stage + 1,), disabled=not can_continue, width="stretch")


def page_compression() -> None:
    st.title("Compression and stiffness")
    team_note("compression")
    t = team()
    if t is None:
        st.info("This page runs a compression test on a virtual pillow made of your team's material. "
                "Pick your team on page 1 first.")
        return
    _, key, _ = t
    ex = MATERIALS[key]["example"]
    unloaded, lofts, name = ex["unloaded"], ex["loft"], short_name(key).lower()
    ss.setdefault("comp_stage", 0)
    ss.setdefault("comp_steps", 0)
    ss.setdefault("anim", 0)
    stage = ss.comp_stage
    stepper(stage)
    st.subheader(COMP_STAGES[stage])
    [stage_rest, stage_head, stage_steps, stage_numbers, stage_curve][stage](key, unloaded, lofts, name)
    st.caption("The virtual pillows are made up to show how each material typically behaves. Your own pillow will "
               "give different numbers, and step 4 is where you enter them.")


def stage_rest(key: str, unloaded: float, lofts: list[float], name: str) -> None:
    c = st.columns([3, 2], gap="large")
    with c[0]:
        rig(rig_svg(key, unloaded, unloaded, unloaded, [], False, False, ss.anim,
                        f"A {name} pillow lying flat with a board on top. Its loft is {unloaded:.0f} mm."))
    with c[1]:
        st.write(f"This {name} pillow is lying flat on a table. A stiff board rests on top so that the weight you "
                 "add later presses evenly across the pillow.")
        st.metric("Unloaded loft", f"{unloaded:.0f} mm")
        st.write("Loft is the pillow's height. Measure it at the centre, from the table to the underside of the "
                 "board, with nothing else on top. This first reading is your starting point: every later reading "
                 "is compared with it.")
    stage_nav(0)


def stage_head(key: str, unloaded: float, lofts: list[float], name: str) -> None:
    loft = lofts[HEAD_STEP]
    c = st.columns([3, 2], gap="large")
    with c[0]:
        rig(rig_svg(key, unloaded, unloaded, loft, [], True, True, ss.anim,
                        f"A 6 kg head is laid on the board. The {name} pillow compresses from {unloaded:.0f} mm "
                        f"to {loft:.0f} mm."))
        st.button("Play again", key="comp_replay", on_click=_set_steps, args=(ss.comp_steps,))
    with c[1]:
        st.write("Now lay a head on it. An adult's head and neck weigh about 8% of body mass (Winter, 2009), so "
                 "for a 75 kg adult that is about 6 kg.")
        m = st.columns(2)
        m[0].metric("Loft with the head on", f"{loft:.0f} mm")
        m[1].metric("Deflection", f"{unloaded - loft:.0f} mm")
        st.write(f"Deflection is how far the pillow compressed: {unloaded:.0f} − {loft:.0f} = "
                 f"{unloaded - loft:.0f} mm. A single head gives you one reading. Is that enough to say how the "
                 "pillow behaves under a heavier or lighter head?")
    stage_nav(1)


def stage_steps(key: str, unloaded: float, lofts: list[float], name: str) -> None:
    k, total = ss.comp_steps, len(EXAMPLE_LOADS_KG)
    blocks = [b - a for a, b in zip([0] + EXAMPLE_LOADS_KG, EXAMPLE_LOADS_KG)][:k]
    before = lofts[k - 2] if k >= 2 else unloaded
    now = lofts[k - 1] if k else unloaded
    c = st.columns([3, 2], gap="large")
    with c[0]:
        rig(rig_svg(key, unloaded, before, now, blocks, False, k > 0, ss.anim,
                        f"{EXAMPLE_LOADS_KG[k - 1] if k else 0} kg on the board. The {name} pillow's loft is "
                        f"{now:.0f} mm."))
    with c[1]:
        st.write("To see how the pillow behaves across the range, add weight in steps and read the loft after "
                 "each one. With real weights, wait the same time (say 30 seconds) before every reading.")
        if k < total:
            step = EXAMPLE_LOADS_KG[k] - (EXAMPLE_LOADS_KG[k - 1] if k else 0)
            b = st.columns(2)
            b[0].button(f"Add {step:g} kg", key="comp_add", type="primary", on_click=_set_steps, args=(k + 1,),
                        width="stretch")
            b[1].button("Add the rest", key="comp_add_all", on_click=_set_steps, args=(total,), width="stretch")
        if k:
            st.dataframe(pd.DataFrame({"Total load (kg)": EXAMPLE_LOADS_KG[:k], "Loft (mm)": lofts[:k]}),
                         hide_index=True, width="stretch")
            if k > HEAD_STEP:
                st.caption("The 6 kg row is the head from the last step.")
            st.button("Take the weights off and start again", key="comp_reset", on_click=_set_steps, args=(0,))
        st.caption(f"Reading {k} of {total}." + ("" if k == total else " Add every weight to continue."))
    stage_nav(2, can_continue=k == total)


def stage_numbers(key: str, unloaded: float, lofts: list[float], name: str) -> None:
    virtual = calcs.compression_curve(example_curve(key), unloaded).table
    shown = virtual.rename(columns={"load_kg": "Load (kg)", "force_n": "Force (N)", "loft_mm": "Loft (mm)",
                                    "deflection_mm": "Deflection (mm)"})
    loft = lofts[HEAD_STEP]
    st.write("A ruler gives you loft in millimetres and the scale gives you mass in kilograms. Stiffness needs "
             "force and deflection, so each reading gets two new columns.")
    c = st.columns([2, 3], gap="large")
    with c[0]:
        st.dataframe(shown[["Load (kg)", "Force (N)", "Loft (mm)", "Deflection (mm)"]].round(1), hide_index=True,
                     width="stretch", height=35 * (len(shown) + 1) + 3)
    with c[1]:
        st.markdown("**Worked example: the head row**")
        st.markdown(f"Force = mass × g = 6 kg × 9.81 m/s² = **{6 * calcs.G:.1f} N**  \n"
                    f"Deflection = unloaded loft − loft = {unloaded:.0f} − {loft:.0f} = **{unloaded - loft:.0f} mm**")
        st.write("The first row is the pillow at rest: no force and no deflection. The app adds it for you.")

    st.markdown("#### Which readings should the curve use?")
    source = st.radio("Which readings should the curve use?", ["virtual", "own"], horizontal=True,
                      label_visibility="collapsed",
                      format_func=lambda s: f"The virtual {name} pillow" if s == "virtual" else "My team's measurements",
                      **bind("comp_source", "virtual"))
    if source == "own":
        st.write("Run the same test at home: the pillow flat, a stiff board on top, then books or water jugs weighed "
                 "on a kitchen scale. Measure the loft at the centre after each step.")
        st.columns(3)[0].number_input("Unloaded loft with the board on (mm)", 10.0, 400.0, step=1.0,
                                      **bind("unloaded_loft", 120.0))
        edited = table_editor("compression", {"load_kg": "Total load added (kg)", "loft_mm": "Loft (mm)"}, 8)
        for p in calcs.compression_curve(edited, ss.unloaded_loft).problems:
            st.warning(p)
    stage_nav(3)


def stage_curve(key: str, unloaded: float, lofts: list[float], name: str) -> None:
    inp = compression_input()
    st.caption("Using " + (f"the virtual {name} pillow." if ss.get("comp_source", "virtual") == "virtual"
                           else "your team's measurements. Change this in step 4."))
    with st.expander("How the regions are labelled (you can change the rule)"):
        st.write("The app finds the softest step and calls it the plateau. Steps before it that are clearly "
                 "stiffer are the initial region. The first step after it that is much stiffer marks the start of "
                 "densification, which is what \"bottoming out\" means.")
        dens_factor = st.slider("Densification starts when stiffness reaches this multiple of the plateau's",
                                1.5, 6.0, step=0.5, **bind("densify_factor", 3.0))
        init_factor = st.slider("Initial region: stiffness at least this multiple of the plateau's", 1.2, 4.0,
                                step=0.1, **bind("initial_factor", 2.0))
    res = None if inp is None else calcs.compression_curve(*inp, dens_factor, init_factor)
    if res is None or res.segments.empty:
        for p in [] if res is None else res.problems:
            st.warning(p)
        st.info("There aren't enough readings to draw the curve yet. Go back to step 4 and check the table.")
        stage_nav(4)
        return

    fig = go.Figure()
    t = res.table
    fig.add_trace(go.Scatter(x=t.deflection_mm, y=t.force_n, mode="lines", line=dict(color="#999", width=1),
                             showlegend=False, hoverinfo="skip"))
    for region, colour in REGION_COLOURS.items():
        part = t[t.region == region]
        if not part.empty:
            fig.add_trace(go.Scatter(x=part.deflection_mm, y=part.force_n, mode="markers", name=region.capitalize(),
                                     marker=dict(color=colour, size=10),
                                     hovertemplate="%{x:.0f} mm, %{y:.0f} N<extra>" + region + "</extra>"))
    if st.checkbox("Overlay the virtual curves for all three materials", **bind("overlay", False)):
        for k2, m in MATERIALS.items():
            ex = calcs.compression_curve(example_curve(k2), m["example"]["unloaded"]).table
            fig.add_trace(go.Scatter(x=ex.deflection_mm, y=ex.force_n, mode="lines", name=f"Virtual: {short_name(k2)}",
                                     line=dict(color=MATERIAL_COLOURS[k2], dash="dash", width=1.5), hoverinfo="skip"))
    fig.add_hline(y=6 * calcs.G, line_dash="dot", line_color="#8a6545", annotation_text="one head (6 kg)",
                  annotation_position="bottom right")
    if res.densification_deflection_mm is not None:
        fig.add_vline(x=res.densification_deflection_mm, line_dash="dot", line_color=REGION_COLOURS["densification"],
                      annotation_text="densification starts", annotation_position="top left")
    fig.update_layout(xaxis_title="Deflection (mm)", yaxis_title="Force (N)", height=420,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, width="stretch")

    c = st.columns(4)
    c[0].metric("Initial stiffness", fmt(res.region_stiffness["initial"], "N/mm", 2))
    c[1].metric("Plateau stiffness", fmt(res.region_stiffness["plateau"], "N/mm", 2))
    c[2].metric("Densification stiffness", fmt(res.region_stiffness["densification"], "N/mm", 2))
    c[3].metric("Densification starts at", fmt(res.densification_force_n, "N", 0))
    with st.expander("Step-by-step stiffness"):
        st.dataframe(res.segments.round(3), width="stretch", hide_index=True)
    st.write(
        "Each dot is one row of the table. Stiffness k is the slope: how many newtons it takes to compress the "
        "pillow one more millimetre. Foams usually give a stiff start, a soft plateau while the cell walls buckle, "
        "and a steep rise once the cells have collapsed. Loose fill like feather often shows no plateau at all, "
        "just steady stiffening."
    )
    st.caption("The dotted line is the head from step 2. Page 6 works out exactly where a sleeper's head sits on "
               "this curve.")
    side_prompt()
    stage_nav(4)


def page_creep() -> None:
    st.title("Creep")
    team_note("creep")
    st.write(
        "Viscoelastic materials keep compressing under a load that doesn't change. Put one load on (close to a "
        "head's weight, about 5 to 7 kg for an adult), read the loft straight away, then again at 1, 2, 5 and "
        "10 minutes."
    )
    c = st.columns(2)
    c[0].number_input("Unloaded loft (mm)", 10.0, 400.0, step=1.0, **bind("creep_unloaded", 120.0))
    c[1].number_input("Load (kg)", 0.5, 50.0, step=0.5, **bind("creep_load", 6.0))
    edited = table_editor("creep", {"time_min": "Time since load went on (min)", "loft_mm": "Loft (mm)"}, 5)
    table, summary = calcs.creep(edited, ss.creep_unloaded)
    if summary is None:
        st.info("Enter at least two readings, with the first one lower than the unloaded loft.")
        return
    fig = go.Figure(go.Scatter(x=table.time_min, y=table.deflection_mm, mode="lines+markers",
                               line=dict(color="#116b68"), marker=dict(size=9)))
    fig.update_layout(xaxis_title="Time (min)", yaxis_title="Deflection (mm)", height=360,
                      margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, width="stretch")
    c = st.columns(3)
    c[0].metric("Deflection at first reading", fmt(summary["initial_deflection_mm"], "mm"))
    c[1].metric(f"Extra deflection over {summary['duration_min']:g} min", fmt(summary["creep_mm"], "mm"))
    c[2].metric("Creep", fmt(summary["creep_percent"], "%"))
    st.write("A spring with no creep would give a flat line. The more the line climbs, the more loft the pillow loses "
             "as the night goes on, before the sleeper even moves.")
    st.caption("Would this pillow creep more or less in a cold bedroom? Which material property decides that?")
    side_prompt()


def page_pressure() -> None:
    st.title("Contact pressure")
    team_note("pressure")
    st.write(
        "Average contact pressure is force divided by contact area. The force on the pillow is roughly the "
        "weight of the head and neck. For the area, have the volunteer lie on the pillow, then trace or measure "
        "the imprint and treat it as an ellipse."
    )
    c = st.columns(3)
    body = c[0].number_input("Sleeper's body mass (kg)", 30.0, 200.0, step=1.0, **bind("body_mass", 75.0))
    force = calcs.head_force_n(body)
    if c[0].checkbox("Use my own head force instead", **bind("force_override", False)):
        force = c[0].number_input("Head force (N)", 1.0, 300.0, step=1.0, **bind("head_force", round(force, 1)))
    length = c[1].number_input("Imprint length (cm)", 1.0, 60.0, step=0.5, **bind("contact_length", 14.0))
    width = c[2].number_input("Imprint width (cm)", 1.0, 60.0, step=0.5, **bind("contact_width", 10.0))
    area = calcs.ellipse_area_cm2(length, width)
    res = calcs.contact_pressure(force, area)
    ss.results["pressure"] = res
    m = st.columns(4)
    m[0].metric("Head force", fmt(res["force_n"], "N"))
    m[1].metric("Contact area", fmt(res["area_cm2"], "cm²", 0))
    m[2].metric("Average pressure", fmt(res["pressure_kpa"], "kPa", 2))
    m[3].metric("Average pressure", fmt(res["pressure_mmhg"], "mmHg"))
    st.caption(f"Head force estimate: body mass × {calcs.HEAD_NECK_MASS_FRACTION} (head and neck segment, Winter 2009) × 9.81 m/s².")

    fig = go.Figure(go.Bar(x=["Your average pressure", "Landis (1930) capillary figure"],
                           y=[res["pressure_mmhg"], calcs.LANDIS_MMHG], marker_color=["#116b68", "#999"],
                           text=[f"{res['pressure_mmhg']:.0f}", f"{calcs.LANDIS_MMHG:.0f}"], textposition="outside"))
    fig.update_layout(yaxis_title="mmHg", height=320, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, width="stretch")
    st.write(
        "Two cautions before you use this number in the debate. It is an average, and the peak under the "
        "cheekbone or ear will be higher. And the 32 mmHg figure was measured in nail-fold capillaries at heart "
        "level in healthy people. It became a \"safe pressure\" benchmark for bed-bound patients who can't move. "
        "Whether it means anything for a healthy sleeper who changes position during the night is for your team to argue."
    )
    side_prompt()


def draw_points(img: Image.Image, pts: list[tuple[float, float]], scale: float) -> Image.Image:
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    r = max(4, int(6 / scale))
    full = [(x / scale, y / scale) for x, y in pts]
    if len(full) >= 2:
        d.line([full[1], full[0]], fill=MARKER_COLOURS[0], width=max(2, int(3 / scale)))
    if len(full) >= 3:
        d.line([full[1], full[2]], fill=MARKER_COLOURS[2], width=max(2, int(3 / scale)))
    for (x, y), colour in zip(full, MARKER_COLOURS):
        d.ellipse([x - r, y - r, x + r, y + r], fill=colour, outline="white")
    return out


def page_neck_angle() -> None:
    st.title("Neck angle")
    team_note("neck")
    st.write(
        "Put small stickers on the volunteer's tragus (the flap in front of the ear canal), C7 (the bony bump at "
        "the base of the neck) and the acromion of the upper shoulder. With them lying on their side on the "
        "pillow, photograph them from directly behind, with the camera level with the neck. Crop out the face "
        "before uploading, and only photograph people who have agreed to it."
    )
    st.write("Click the three markers in order: **tragus, then C7, then the upper acromion**.")
    photo = st.file_uploader("Photo (JPG or PNG)", type=["jpg", "jpeg", "png"])
    if st.button("Clear points"):
        ss.points, ss.last_click = [], None

    if photo is not None:
        from streamlit_image_coordinates import streamlit_image_coordinates

        img = ImageOps.exif_transpose(Image.open(photo))  # phone photos often carry rotation in EXIF
        display_w = min(700, img.width)
        scale = display_w / img.width
        shown = draw_points(img, ss.points, scale).resize((display_w, int(img.height * scale)))
        click = streamlit_image_coordinates(shown, key="neck_click")
        if click and click != ss.last_click and len(ss.points) < 3:
            ss.last_click = click
            ss.points.append((float(click["x"]), float(click["y"])))
            st.rerun()
    else:
        st.caption("No photo? Load the example data on page 1 to see how the angle is worked out, "
                   "or type pixel coordinates below.")

    with st.expander("Type or correct the coordinates (pixels, y increases downward)"):
        pts = list(ss.points) + [(0.0, 0.0)] * (3 - len(ss.points))
        cols = st.columns(3)
        typed = []
        for i, (name, (x, y)) in enumerate(zip(MARKERS, pts)):
            cols[i].markdown(f"**{name}**")
            tx = cols[i].number_input("x", value=float(x), key=f"pt{i}x_{ss.editor_version}_{len(ss.points)}")
            ty = cols[i].number_input("y", value=float(y), key=f"pt{i}y_{ss.editor_version}_{len(ss.points)}")
            typed.append((tx, ty))
        if st.button("Use these coordinates"):
            ss.points = typed
            st.rerun()

    if len(ss.points) < 3:
        st.info(f"{len(ss.points)} of 3 points placed.")
        return
    try:
        res = calcs.neck_angle(*ss.points)
    except ValueError as e:
        st.error(str(e))
        return
    ss.results["neck"] = {**res, "points_px": {m: p for m, p in zip(MARKERS, ss.points)}}

    xs, ys = zip(*ss.points)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[xs[1], xs[0]], y=[ys[1], ys[0]], mode="lines", line=dict(color=MARKER_COLOURS[0], width=3), name="Neck line"))
    fig.add_trace(go.Scatter(x=[xs[1], xs[2]], y=[ys[1], ys[2]], mode="lines", line=dict(color=MARKER_COLOURS[2], width=3), name="Shoulder line"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", text=["Tragus", "C7", "Acromion"], textposition="top center",
                             marker=dict(size=12, color=list(MARKER_COLOURS)), showlegend=False))
    fig.update_yaxes(autorange="reversed", scaleanchor="x", scaleratio=1, showticklabels=False)
    fig.update_xaxes(showticklabels=False)
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")

    c = st.columns(2)
    c[0].metric("Angle between neck and shoulder lines", fmt(res["angle_deg"], "°"))
    lf = res["lateral_flexion_deg"]
    direction = "toward the mattress (pillow too low)" if lf > 0 else "away from the mattress (pillow too high)" if lf < 0 else "level"
    c[1].metric("Lateral flexion from level", fmt(abs(lf), "°"), help="0° means the neck lines up with the trunk.")
    st.write(f"The head is tilted {abs(lf):.1f}° {direction}.")
    st.caption("This assumes the shoulder line (C7 to the upper acromion) is perpendicular to the trunk's long axis "
               "and that the camera is square to the person. The tragus is not on the midline, so this is an "
               "approximation of the cervical spine's angle. Facet joint loading can't be read from a photo. "
               "Researchers estimate it with models that start from angles like this one.")


def page_loft() -> None:
    st.title("Loft fit")
    team_note("loft")
    st.write(
        "For a side-sleeper, the pillow has to fill the gap between the mattress and the head so the head's "
        "midline stays level with the neck's. The gap depends on the sleeper's shoulder, how far that shoulder "
        "sinks into the mattress, and the width of the head and neck."
    )
    c = st.columns(2)
    with c[0]:
        st.subheader("The sleeper")
        body = st.number_input("Body mass (kg)", 30.0, 200.0, step=1.0, **bind("body_mass", 75.0))
        a2n = st.number_input("Acromion to side of neck (cm)", 5.0, 30.0, step=0.5, **bind("a2n", 14.0),
                              help="Measured standing, from the edge of the shoulder to the side of the neck.")
        neck_w = st.number_input("Neck width, side to side (cm)", 6.0, 20.0, step=0.5, **bind("neck_w", 11.0))
        head_w = st.number_input("Head width at the ears (cm)", 10.0, 22.0, step=0.5, **bind("head_w", 15.0))
        mattress = st.selectbox("Mattress", list(calcs.SHOULDER_SINK_CM), **bind("mattress", "Medium-firm"))
        sink = st.number_input("Shoulder sink into the mattress (cm)", 0.0, 15.0, step=0.5,
                               **bind(f"sink_{mattress}", calcs.SHOULDER_SINK_CM[mattress]),
                               help="The default for each mattress type is a rough starting value, not a measurement. "
                                    "Replace it if you can measure it.")
    need_cm = calcs.required_loft_cm(a2n, neck_w, head_w, sink)
    force = calcs.head_force_n(body)
    with c[1]:
        st.subheader("The pillow")
        st.metric("Loft needed under the head", fmt(need_cm, "cm"))
        comp = compression_result()
        if comp is None:
            st.info("Enter compression data on page 2 to check whether your tested pillow fits.")
            loaded_cm = None
        else:
            defl = calcs.deflection_at_force(comp.table, force)
            if defl is None:
                st.warning(f"The head force ({force:.0f} N) is outside the loads you tested. Add heavier steps on page 2.")
                loaded_cm = None
            else:
                unloaded = compression_input()[1]
                loaded_cm = (unloaded - defl) / 10
                st.metric("Your pillow's loft under this head", fmt(loaded_cm, "cm"),
                          delta=f"{loaded_cm - need_cm:+.1f} cm vs needed", delta_color="off")
                region = comp.table.region.iloc[int((comp.table.force_n - force).abs().idxmin())]
                st.write(f"Under {force:.0f} N the pillow compresses {defl:.0f} mm, which puts the head in the "
                         f"**{region}** region of its curve.")
    ss.results["loft"] = {"body_mass_kg": body, "acromion_to_neck_cm": a2n, "neck_width_cm": neck_w,
                          "head_width_cm": head_w, "mattress": mattress, "shoulder_sink_cm": sink,
                          "head_force_n": force, "required_loft_cm": need_cm, "tested_pillow_loaded_loft_cm": loaded_cm}

    st.subheader("How the needed loft is worked out")
    st.markdown(
        f"Side of neck above the mattress = {a2n:g} − {sink:g} = {a2n - sink:g} cm  \n"
        f"Neck midline = that + half the neck width = {a2n - sink + neck_w / 2:g} cm  \n"
        f"Underside of head = neck midline − half the head width = **{need_cm:g} cm**"
    )
    st.caption("The compression test used a flat board, but a head is curved and presses into a smaller area, so "
               "the real deflection under a head will differ. Say which way you expect it to differ, and why, in "
               "your specification.")
    side_prompt()


def page_export() -> None:
    st.title("Export results")
    r = dict(ss.results)
    if (comp := compression_result()) is not None:
        r["compression"] = comp
    if (cr := creep_result()) is not None:
        r["creep"] = cr
    done = {"Team": team() is not None, "Compression": "compression" in r,
            "Creep": "creep" in r, "Contact pressure": "pressure" in r, "Neck angle": "neck" in r, "Loft fit": "loft" in r}
    st.write("  \n".join(f"{'✅' if ok else '⬜'} {name}" for name, ok in done.items()))
    names = st.text_input("Your name or your team's names", **bind("names", ""))

    buf = io.BytesIO()
    t = team()
    summary: dict = {"exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "names": names,
                     "team": None if t is None else {"number": t[0], "material": MATERIALS[t[1]]["name"], "side": t[2]}}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if "compression" in r:
            c = r["compression"]
            z.writestr("compression_curve.csv", c.table.to_csv(index=False))
            z.writestr("compression_steps.csv", c.segments.to_csv(index=False))
            summary["compression"] = {"data_source": "virtual pillow (made-up example)" if ss.get("comp_source", "virtual") == "virtual" else "team measurements",
                                      "unloaded_loft_mm": compression_input()[1], "region_stiffness_n_per_mm": c.region_stiffness,
                                      "densification_force_n": c.densification_force_n,
                                      "densification_deflection_mm": c.densification_deflection_mm}
        if "creep" in r:
            table, s, load = r["creep"]
            z.writestr("creep.csv", table.to_csv(index=False))
            summary["creep"] = {**s, "load_kg": load}
        if "pressure" in r:
            summary["contact_pressure"] = r["pressure"]
        if "neck" in r:
            summary["neck_angle"] = r["neck"]
        if "loft" in r:
            summary["loft_fit"] = r["loft"]
        z.writestr("summary.json", json.dumps(summary, indent=2, default=float))
    name = f"team{t[0]}" if t else "".join(ch for ch in names if ch.isalnum() or ch in "-_") or "results"
    st.download_button("Download results ZIP", buf.getvalue(), f"pillow_debate_{name}.zip", "application/zip",
                       type="primary", disabled=not any(done.values()))
    st.caption("Include the CSV tables in your specification as your raw calculation tables. Photos are not included "
               "in the ZIP. The claim audit is done on paper, so it isn't part of this export.")


RENDER = [page_start, page_team, page_compression, page_creep, page_pressure, page_neck_angle, page_loft, page_export]

with st.sidebar:
    st.markdown("### Pillow debate")
    choice = st.radio("Section", [p[0] for p in PAGES], captions=[p[1] for p in PAGES], label_visibility="collapsed")
    st.caption("Clinical biomechanics assignment concept. Prototype for discussion.")

RENDER[[p[0] for p in PAGES].index(choice)]()
