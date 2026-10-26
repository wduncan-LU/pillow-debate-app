"""Calculations for the pillow debate app. No Streamlit imports, so tests can call these directly."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

G = 9.81
MMHG_PER_KPA = 7.50062
# Winter, Biomechanics and Motor Control of Human Movement (4th ed., 2009): head and neck = 0.081 of body mass.
HEAD_NECK_MASS_FRACTION = 0.081
# Classic capillary benchmark (Landis 1930, nail-fold capillaries at heart level). Shown for debate, not as a limit.
LANDIS_MMHG = 32.0
# Rough starting values for how far the lower shoulder sinks into the mattress. Not measured; students should replace them.
SHOULDER_SINK_CM = {"Soft": 4.0, "Medium-firm": 2.5, "Firm": 1.5}

REGION_LABELS = ("initial", "plateau", "densification")


@dataclass
class CompressionResult:
    table: pd.DataFrame  # one row per load step
    segments: pd.DataFrame  # one row per pair of neighbouring steps
    region_stiffness: dict[str, float | None]  # secant stiffness for each region, N/mm
    densification_force_n: float | None
    densification_deflection_mm: float | None
    problems: list[str]


def compression_curve(raw: pd.DataFrame, unloaded_loft_mm: float, densify_factor: float = 3.0,
                      initial_factor: float = 2.0) -> CompressionResult:
    """Turn a load (kg) / loft (mm) table into a force-deflection curve with labelled regions.

    Each segment's stiffness is the slope between neighbouring load steps. The softest segment marks the
    plateau. Segments before it that are at least `initial_factor` times stiffer are the initial region,
    and the first segment after it that is at least `densify_factor` times stiffer starts densification.
    """
    problems: list[str] = []
    df = raw[["load_kg", "loft_mm"]].apply(pd.to_numeric, errors="coerce").dropna()
    df = df.sort_values("load_kg").drop_duplicates("load_kg").reset_index(drop=True)
    if df.empty or df.load_kg.iloc[0] > 0:
        df = pd.concat([pd.DataFrame({"load_kg": [0.0], "loft_mm": [unloaded_loft_mm]}), df], ignore_index=True)
    df["force_n"] = df.load_kg * G
    df["deflection_mm"] = unloaded_loft_mm - df.loft_mm
    df["region"] = ""

    empty = CompressionResult(df, pd.DataFrame(), {r: None for r in REGION_LABELS}, None, None, problems)
    if len(df) < 4:
        problems.append("Enter at least three loaded steps to see the shape of the curve.")
        return empty

    d_force = np.diff(df.force_n.to_numpy())
    d_defl = np.diff(df.deflection_mm.to_numpy())
    bad = np.flatnonzero(d_defl <= 0)
    if bad.size:
        loads = ", ".join(f"{df.load_kg.iloc[i + 1]:g} kg" for i in bad)
        problems.append(f"Loft did not drop between some steps (at {loads}). Re-measure those, because "
                        "a pillow cannot get taller when you add weight.")
        return empty

    seg = pd.DataFrame({
        "from_kg": df.load_kg.iloc[:-1].to_numpy(),
        "to_kg": df.load_kg.iloc[1:].to_numpy(),
        "stiffness_n_per_mm": d_force / d_defl,
    })
    k = seg.stiffness_n_per_mm.to_numpy()
    soft = int(np.argmin(k))
    labels = ["plateau"] * len(k)
    for i in range(soft):
        if k[i] >= initial_factor * k[soft]:
            labels[i] = "initial"
        else:
            break
    dens = next((i for i in range(soft + 1, len(k)) if k[i] >= densify_factor * k[soft]), None)
    if dens is not None:
        for i in range(dens, len(k)):
            labels[i] = "densification"
    seg["region"] = labels
    # A point takes the label of the segment that ends at it; the unloaded point takes the first segment's.
    df["region"] = [labels[0]] + labels

    region_k: dict[str, float | None] = {}
    for name in REGION_LABELS:
        idx = [i for i, lab in enumerate(labels) if lab == name]
        if idx:
            f0, f1 = df.force_n.iloc[idx[0]], df.force_n.iloc[idx[-1] + 1]
            x0, x1 = df.deflection_mm.iloc[idx[0]], df.deflection_mm.iloc[idx[-1] + 1]
            region_k[name] = float((f1 - f0) / (x1 - x0))
        else:
            region_k[name] = None
    dens_f = float(df.force_n.iloc[dens]) if dens is not None else None
    dens_x = float(df.deflection_mm.iloc[dens]) if dens is not None else None
    if dens is None:
        problems.append("No densification found in the loads you tested. Either the pillow never bottomed out, "
                        "or the heaviest load was not heavy enough to show it.")
    return CompressionResult(df, seg, region_k, dens_f, dens_x, problems)


def deflection_at_force(curve: pd.DataFrame, force_n: float) -> float | None:
    """Interpolate deflection (mm) at a given force. None if the force is outside the tested range."""
    if curve.empty or force_n < curve.force_n.min() or force_n > curve.force_n.max():
        return None
    return float(np.interp(force_n, curve.force_n, curve.deflection_mm))


def creep(raw: pd.DataFrame, unloaded_loft_mm: float) -> tuple[pd.DataFrame, dict[str, float] | None]:
    """Deflection over time under a constant load. Summary compares the last reading with the first."""
    df = raw[["time_min", "loft_mm"]].apply(pd.to_numeric, errors="coerce").dropna()
    df = df.sort_values("time_min").reset_index(drop=True)
    df["deflection_mm"] = unloaded_loft_mm - df.loft_mm
    if len(df) < 2 or df.deflection_mm.iloc[0] <= 0:
        return df, None
    first, last = df.deflection_mm.iloc[0], df.deflection_mm.iloc[-1]
    return df, {
        "initial_deflection_mm": float(first),
        "final_deflection_mm": float(last),
        "creep_mm": float(last - first),
        "creep_percent": float(100 * (last - first) / first),
        "duration_min": float(df.time_min.iloc[-1] - df.time_min.iloc[0]),
    }


def head_force_n(body_mass_kg: float) -> float:
    return body_mass_kg * HEAD_NECK_MASS_FRACTION * G


def ellipse_area_cm2(length_cm: float, width_cm: float) -> float:
    return math.pi / 4 * length_cm * width_cm


def contact_pressure(force_n: float, area_cm2: float) -> dict[str, float]:
    kpa = force_n / (area_cm2 / 1e4) / 1000
    return {"force_n": force_n, "area_cm2": area_cm2, "pressure_kpa": kpa, "pressure_mmhg": kpa * MMHG_PER_KPA}


def neck_angle(tragus: tuple[float, float], c7: tuple[float, float], acromion: tuple[float, float]) -> dict[str, float]:
    """Angle at C7 between the neck line (C7 to tragus) and the shoulder line (C7 to the upper acromion).

    In a posterior photo of a side-lying person, a neck held level with the trunk puts these lines at
    about 90 degrees. Above 90 means the head has dropped toward the mattress (pillow too low), below 90
    means it is pushed up (pillow too high). Works in any pixel units, since only the angle is used.
    """
    a = np.subtract(tragus, c7)
    b = np.subtract(acromion, c7)
    if not np.linalg.norm(a) or not np.linalg.norm(b):
        raise ValueError("Two of the points are in the same place.")
    cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    return {"angle_deg": angle, "lateral_flexion_deg": angle - 90}


def required_loft_cm(acromion_to_neck_cm: float, neck_width_cm: float, head_width_cm: float,
                     shoulder_sink_cm: float) -> float:
    """Loaded pillow height that keeps the head's midline level with the neck's midline in side-lying.

    The side of the neck sits (acromion-to-neck distance - shoulder sink) above the mattress. The neck's
    midline is half a neck width higher, and the underside of the head must sit half a head width below that.
    """
    return acromion_to_neck_cm - shoulder_sink_cm + neck_width_cm / 2 - head_width_cm / 2
