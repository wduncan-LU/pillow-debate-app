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
    ("2. Compression and stiffness", "Force-deflection curve from stacked weights"),
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


def team_label(n: int) -> str:
    key, side = TEAMS[n]
    return f"Team {n} · {MATERIALS[key]['name'].split(' (')[0]} · {side}"


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
    ex = MATERIALS[key]["example"]
    ss.unloaded_loft = ex["unloaded"]
    ss.compression_saved = example_curve(key)
    ss.creep_unloaded = ex["unloaded"]
    ss.creep_load = EXAMPLE_CREEP_KG
    ss.creep_saved = pd.DataFrame({"time_min": EXAMPLE_CREEP_MIN, "loft_mm": ex["creep"]}).astype(float)
    ss.contact_length, ss.contact_width = ex["contact"]
    ss.points = [ex["tragus"], *SHOULDER_POINTS]
    ss.results = {}
    ss.editor_version += 1


def compression_result() -> calcs.CompressionResult | None:
    """Recomputed from saved inputs so later pages don't depend on page 2 having been opened."""
    if "compression_saved" not in ss:
        return None
    res = calcs.compression_curve(ss.compression_saved, ss.get("unloaded_loft", 120.0),
                                  ss.get("densify_factor", 3.0), ss.get("initial_factor", 2.0))
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
    st.columns(2)[0].selectbox("Your team", sorted(TEAMS), format_func=team_label, placeholder="Choose your team",
                               **bind("team_no", None))
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
    st.write(f"The example is a made-up {m['name'].split(' (')[0].lower()} pillow. Load it to see what each page "
             "does, then replace it with your own measurements. Loading it overwrites anything you've entered.")
    if st.button("Load example data", type="primary"):
        load_example(key)
        st.success("Example data loaded into pages 2 to 6.")


def page_compression() -> None:
    st.title("Compression and stiffness")
    team_note("compression")
    st.write(
        "Lay the pillow flat. Put a stiff board on top so the load spreads evenly, measure the loft (height) "
        "with nothing else on it, then add weights in steps (books or water jugs weighed on a kitchen scale). "
        "Measure the loft at the centre after each step, waiting the same time (say 30 s) before each reading."
    )
    st.columns(3)[0].number_input("Unloaded loft with the board on (mm)", 10.0, 400.0, step=1.0,
                                  **bind("unloaded_loft", 120.0))
    edited = table_editor("compression", {"load_kg": "Total load added (kg)", "loft_mm": "Loft (mm)"}, 8)
    with st.expander("How the regions are labelled (you can change the rule)"):
        st.write("The app finds the softest step and calls it the plateau. Steps before it that are clearly "
                 "stiffer are the initial region. The first step after it that is much stiffer marks the start of "
                 "densification, which is what \"bottoming out\" means.")
        dens_factor = st.slider("Densification starts when stiffness reaches this multiple of the plateau's",
                                1.5, 6.0, step=0.5, **bind("densify_factor", 3.0))
        init_factor = st.slider("Initial region: stiffness at least this multiple of the plateau's", 1.2, 4.0,
                                step=0.1, **bind("initial_factor", 2.0))

    res = calcs.compression_curve(edited, ss.unloaded_loft, dens_factor, init_factor)
    for p in res.problems:
        st.warning(p)
    if res.segments.empty:
        return

    fig = go.Figure()
    t = res.table
    fig.add_trace(go.Scatter(x=t.deflection_mm, y=t.force_n, mode="lines", line=dict(color="#999", width=1),
                             showlegend=False, hoverinfo="skip"))
    for name, colour in REGION_COLOURS.items():
        part = t[t.region == name]
        if not part.empty:
            fig.add_trace(go.Scatter(x=part.deflection_mm, y=part.force_n, mode="markers", name=name.capitalize(),
                                     marker=dict(color=colour, size=10),
                                     hovertemplate="%{x:.0f} mm, %{y:.0f} N<extra>" + name + "</extra>"))
    if st.checkbox("Overlay the example curves for all three materials", **bind("overlay", False)):
        for key, m in MATERIALS.items():
            ex = calcs.compression_curve(example_curve(key), m["example"]["unloaded"]).table
            fig.add_trace(go.Scatter(x=ex.deflection_mm, y=ex.force_n, mode="lines", name=f"Example: {m['name'].split(' (')[0]}",
                                     line=dict(color=MATERIAL_COLOURS[key], dash="dash", width=1.5), hoverinfo="skip"))
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
        "Stiffness k is the slope: how many newtons it takes to compress the pillow one more millimetre. "
        "Foams usually give a stiff start, a soft plateau while the cell walls buckle, and a steep rise once the "
        "cells have collapsed. Loose fill like feather often shows no plateau at all, just steady stiffening."
    )
    st.caption("Where on this curve does a sleeper's head sit? Page 6 works that out. The example curves are made "
               "up to show typical shapes, so argue from your own measurements.")
    side_prompt()


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
                unloaded = float(ss.get("unloaded_loft", 0))
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
            summary["compression"] = {"unloaded_loft_mm": ss.get("unloaded_loft"), "region_stiffness_n_per_mm": c.region_stiffness,
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
