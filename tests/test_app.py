"""Headless walk through every page with Streamlit AppTest. Checks that pages render and that the
example data flows from the Start page into the later pages. It does not check the photo click
component, which AppTest can't drive."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def fresh():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    return at


def go(at, label):
    at.sidebar.radio[0].set_value(label).run()
    assert not at.exception, at.exception
    return at


def metrics(at):
    return {m.label: m.value for m in at.metric}


def test_every_page_renders_empty():
    at = fresh()
    for label in at.sidebar.radio[0].options:
        go(at, label)
        assert at.title, label


def pick_team(at, n):
    go(at, "1. Your team")
    at.selectbox[0 if n % 2 else 1].set_value(n).run()  # FOR teams are odd, AGAINST even
    assert not at.exception, at.exception
    return at


def click(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


def to_stage(at, stage):
    for i in range(at.session_state["comp_stage"], stage):
        click(at, f"comp_next_{i}")
    return at


def test_example_flows_through_pages():
    at = pick_team(fresh(), 3)  # memory foam, FOR
    assert any("The FOR case (your side)" in m.value for m in at.markdown)
    at.button[0].click().run()  # Load example data
    assert any("Example data loaded" in s.value for s in at.success)

    go(at, "2. Compression and stiffness")
    assert any("Team 3 · Memory foam · FOR" in c.value for c in at.caption)
    assert any("three regions" in i.value for i in at.info)
    assert metrics(at)["Unloaded loft"] == "120 mm"
    to_stage(at, 4)
    m = metrics(at)
    assert m["Plateau stiffness"] != "n/a"
    assert m["Densification starts at"] == "78 N"

    go(at, "3. Creep")
    assert metrics(at)["Creep"] == "14.0 %"

    go(at, "4. Contact pressure")
    mm = [m.value for m in at.metric if m.label == "Average pressure"]
    assert "40.7 mmHg" in mm

    go(at, "5. Neck angle")
    assert metrics(at)["Lateral flexion from level"] == "4.4 °"

    # Loft fit uses page 2's saved data even though page 2's widgets are no longer drawn.
    go(at, "6. Loft fit")
    m = metrics(at)
    assert m["Loft needed under the head"] == "9.5 cm"
    assert m["Your pillow's loft under this head"] == "7.0 cm"

    go(at, "7. Export results")
    button = at.get("download_button")[0]
    assert not button.proto.disabled


def test_each_material_example_misfits_differently():
    expected = {1: "6.8 cm", 3: "7.0 cm", 5: "10.6 cm"}  # needed is 9.5 cm: feather and foam low, latex high
    for n, loft in expected.items():
        at = pick_team(fresh(), n)
        at.button[0].click().run()
        go(at, "6. Loft fit")
        assert metrics(at)["Your pillow's loft under this head"] == loft, n


def test_team_survives_page_changes():
    at = pick_team(fresh(), 6)
    go(at, "4. Contact pressure")
    assert any("Team 6 · Perforated elastomer · AGAINST" in c.value for c in at.caption)
    assert any("find the weakness" in c.value for c in at.caption)
    go(at, "1. Your team")
    assert at.selectbox[0].value is None
    assert at.selectbox[1].value == 6


def test_choosing_one_side_clears_the_other():
    at = pick_team(fresh(), 4)
    at = pick_team(at, 5)
    assert at.selectbox[0].value == 5
    assert at.selectbox[1].value is None


def test_virtual_compression_walkthrough():
    at = pick_team(fresh(), 1)  # feather
    go(at, "2. Compression and stiffness")
    click(at, "comp_next_0")
    m = metrics(at)
    assert m["Loft with the head on"] == "68 mm"
    assert m["Deflection"] == "72 mm"
    click(at, "comp_next_1")
    assert at.button(key="comp_next_2").proto.disabled
    click(at, "comp_add")
    assert len(at.dataframe[0].value) == 1
    click(at, "comp_add_all")
    assert len(at.dataframe[0].value) == 10
    assert not at.button(key="comp_next_2").proto.disabled
    to_stage(at, 4)
    assert metrics(at)["Densification starts at"] != "n/a"
    click(at, "comp_back_4")
    assert at.session_state["comp_stage"] == 3


def test_page_two_needs_a_team():
    at = go(fresh(), "2. Compression and stiffness")
    assert any("Pick your team" in i.value for i in at.info)


def test_values_survive_page_changes():
    at = pick_team(fresh(), 2)
    at.session_state["comp_stage"] = 3
    go(at, "2. Compression and stiffness")
    at.radio(key="w_comp_source").set_value("own").run()
    at.number_input[0].set_value(150.0).run()
    go(at, "4. Contact pressure")
    go(at, "2. Compression and stiffness")
    assert at.number_input[0].value == 150.0
