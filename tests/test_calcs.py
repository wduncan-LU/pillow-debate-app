import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import calcs  # noqa: E402


def three_region_table():
    # Built so the segment stiffnesses are 2.0 (initial), 0.5 (plateau) and 4.0 (densification) N/mm.
    # Each step is 1 kg = 9.81 N, so deflection steps are 9.81 / k mm.
    steps = [2.0, 0.5, 0.5, 4.0, 4.0]
    loft, lofts = 120.0, []
    for k in steps:
        loft -= calcs.G / k
        lofts.append(loft)
    return pd.DataFrame({"load_kg": [1, 2, 3, 4, 5], "loft_mm": lofts})


def test_regions_and_stiffness():
    res = calcs.compression_curve(three_region_table(), 120.0)
    assert list(res.segments.region) == ["initial", "plateau", "plateau", "densification", "densification"]
    assert res.region_stiffness["initial"] == pytest.approx(2.0)
    assert res.region_stiffness["plateau"] == pytest.approx(0.5)
    assert res.region_stiffness["densification"] == pytest.approx(4.0)
    assert res.densification_force_n == pytest.approx(3 * calcs.G)
    assert not res.problems


def test_densify_factor_changes_onset():
    res = calcs.compression_curve(three_region_table(), 120.0, densify_factor=10.0)
    assert res.densification_force_n is None
    assert any("No densification" in p for p in res.problems)


def test_zero_load_row_added_and_unsorted_input():
    t = three_region_table().iloc[::-1]
    res = calcs.compression_curve(t, 120.0)
    assert res.table.load_kg.iloc[0] == 0
    assert res.table.deflection_mm.iloc[0] == 0


def test_loft_increase_is_flagged():
    t = pd.DataFrame({"load_kg": [1, 2, 3], "loft_mm": [110, 112, 100]})
    res = calcs.compression_curve(t, 120.0)
    assert res.segments.empty
    assert any("did not drop" in p for p in res.problems)


def test_too_few_rows_and_blank_rows():
    t = pd.DataFrame({"load_kg": [1, None, 2], "loft_mm": [110, None, 100]})
    res = calcs.compression_curve(t, 120.0)
    assert res.segments.empty
    assert res.problems


def test_deflection_interpolation_and_range():
    res = calcs.compression_curve(three_region_table(), 120.0)
    mid = (res.table.force_n.iloc[1] + res.table.force_n.iloc[2]) / 2
    expected = (res.table.deflection_mm.iloc[1] + res.table.deflection_mm.iloc[2]) / 2
    assert calcs.deflection_at_force(res.table, mid) == pytest.approx(expected)
    assert calcs.deflection_at_force(res.table, 1e6) is None


def test_creep_summary():
    t = pd.DataFrame({"time_min": [0, 1, 10], "loft_mm": [90, 88, 85]})
    _, s = calcs.creep(t, 120.0)
    assert s["initial_deflection_mm"] == 30
    assert s["creep_mm"] == 5
    assert s["creep_percent"] == pytest.approx(100 * 5 / 30)
    assert s["duration_min"] == 10


def test_pressure_units():
    # 100 N over 100 cm^2 = 10 kPa = 75.0 mmHg
    r = calcs.contact_pressure(100.0, 100.0)
    assert r["pressure_kpa"] == pytest.approx(10.0)
    assert r["pressure_mmhg"] == pytest.approx(75.0062)
    assert calcs.ellipse_area_cm2(2, 2) == pytest.approx(math.pi)


def test_head_force():
    assert calcs.head_force_n(75) == pytest.approx(75 * 0.081 * 9.81)


def test_neck_angle_level_and_drooped():
    # Posterior photo, y increases downward. C7 at origin, upper acromion straight up, tragus to the right.
    level = calcs.neck_angle((100, 0), (0, 0), (0, -100))
    assert level["angle_deg"] == pytest.approx(90)
    assert level["lateral_flexion_deg"] == pytest.approx(0)
    # Head dropped 10 degrees toward the mattress (downward in the image).
    t = (100 * math.cos(math.radians(10)), 100 * math.sin(math.radians(10)))
    drooped = calcs.neck_angle(t, (0, 0), (0, -100))
    assert drooped["lateral_flexion_deg"] == pytest.approx(10)
    # Mirror image (lying on the other side) gives the same answer.
    mirrored = calcs.neck_angle((-t[0], t[1]), (0, 0), (0, -100))
    assert mirrored["lateral_flexion_deg"] == pytest.approx(10)
    with pytest.raises(ValueError):
        calcs.neck_angle((0, 0), (0, 0), (0, -1))


def test_required_loft():
    # 14 - 2.5 + 5.5 - 7.5 = 9.5 cm for the assignment's sleeper with the default widths.
    assert calcs.required_loft_cm(14, 11, 15, 2.5) == pytest.approx(9.5)
