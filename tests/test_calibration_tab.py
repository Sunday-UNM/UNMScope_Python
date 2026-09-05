"""The um/V calibration tab, headless: LabVIEW display format, the derived
indicator (event [1] -> Update Values), Save (-> ini copy + `saved`
Calibration, LouisXIV's Force Waveform Recalc), Revert, bad input, and the
measured layout against the LabVIEW render."""
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from unmscope.config.um_per_volt import MicronsToVolt
from unmscope.gui import calibration_tab as ct
from unmscope.gui.calibration_tab import CalibrationTab, format_value

from tests.test_um_per_volt import INI


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ini(tmp_path):
    p = tmp_path / "SPIMProject.ini"
    p.write_bytes(INI)
    return p


@pytest.fixture
def tab(app, ini):
    logs = []
    t = CalibrationTab(ini_path=ini, log=logs.append)
    t.logs = logs
    yield t
    t.deleteLater()


def edit(tab, name, text):
    f = tab.fields[name]
    f.setText(text)
    f.editingFinished.emit()


def test_fields_show_the_ini_in_labview_display_format(tab):
    assert [tab.fields[n].text() for n in MicronsToVolt.field_names()] == \
        ["2000", "1", "NaN", "7", "8", "1", "100", "10", "0"]
    assert tab.indicator.text() == "2000" and tab.indicator.isReadOnly()
    assert [lab.text() for lab in tab.row_labels.values()] == list(MicronsToVolt.keys())
    assert tab.cluster_label.text() == "Microns to Volt calibrations"
    assert tab.indicator_label.text() == "Galvo Pos um/Galvo Pos Volt"
    assert tab.save_button.text() == "Save" and tab.revert_button.text() == "Revert"
    assert not hasattr(tab, "close_button")          # Close dropped: meaningless in a tab
    assert format_value(0.5) == "0.5" and format_value(-10.0) == "-10" and format_value(52.3861) == "52.3861"


def test_edit_updates_the_derived_indicator_only(tab, ini):
    before = ini.read_bytes()
    seen = []
    tab.changed.connect(seen.append)
    edit(tab, "galvo_position_ratio", "0.5")
    assert tab.indicator.text() == "1000"            # 2000 x 0.5, "Update Values"
    edit(tab, "galvo_cmd_x", "53")
    assert tab.indicator.text() == "26.5"
    assert ini.read_bytes() == before                # nothing written until Save
    assert [m.galvo_pos_um_per_pos_v for m in seen] == [1000.0, 26.5]
    assert tab.values().galvo_cmd_x == 53.0


def test_save_writes_the_copy_and_emits_the_reloaded_calibration(tab, ini):
    got = []
    tab.saved.connect(got.append)
    edit(tab, "galvo_cmd_x", "1500")
    edit(tab, "galvo_cmd_y", "nan")
    edit(tab, "zpiezo", "-9.5")
    assert tab.save() is True
    text = ini.read_bytes()
    assert b"Galvo cmd X um/X Volt = 1500.000000\r\n" in text
    assert b"Zpiezo um/Zpiezo Volt = -9.500000\r\n" in text
    assert b"Galvo cmd Y um/Y Volt = NaN\r\n" in text
    assert b"# of beams = 10" in text                # the rest of the file is intact
    assert len(got) == 1
    cal = got[0]
    assert cal.x_galvo.um_per_volt == 1500.0 and cal.z_piezo.um_per_volt == -9.5
    assert cal.um_per_volt.galvo_cmd_x == 1500.0 and math.isnan(cal.um_per_volt.galvo_cmd_y)
    assert cal.x_galvo.v_max == 2.5                  # limits still from the (defaulted) sections
    assert cal.source == str(ini)
    assert any("saved to" in m for m in tab.logs)


def test_save_commits_a_field_that_was_not_yet_committed(tab, ini):
    tab.fields["dither_galvo"].setText("12")         # typed, no Enter / focus-out yet
    assert tab.save()
    assert b"Dither Galvo um/V = 12.000000\r\n" in ini.read_bytes()


def test_revert_reloads_from_disk(tab, ini):
    edit(tab, "galvo_cmd_z", "99")
    assert tab.fields["galvo_cmd_z"].text() == "99"
    tab.revert_button.click()
    assert tab.fields["galvo_cmd_z"].text() == "7" and tab.values().galvo_cmd_z == 7.0
    ini.write_bytes(INI.replace(b"Galvo cmd Z um/Z Volt = 7.000000", b"Galvo cmd Z um/Z Volt = 6.000000"))
    tab.reload()
    assert tab.fields["galvo_cmd_z"].text() == "6"


def test_invalid_text_is_put_back(tab):
    f = tab.fields["galvo_cmd_x"]
    f.setText("")
    f.editingFinished.emit()
    assert f.text() == "2000" and tab.values().galvo_cmd_x == 2000.0
    from PySide6.QtGui import QValidator
    ok = QValidator.State.Acceptable
    assert f.validator().validate("12abc", 0)[0] != ok
    assert f.validator().validate("NaN", 0)[0] == ok
    assert f.validator().validate("-1.5e3", 0)[0] == ok


def test_save_failure_is_logged_not_raised(tab, ini, monkeypatch):
    got = []
    tab.saved.connect(got.append)
    monkeypatch.setattr(MicronsToVolt, "save", lambda self, path=None: (_ for _ in ()).throw(OSError("disk")))
    assert tab.save() is False and not got
    assert any("save failed" in m for m in tab.logs)


def test_layout_matches_the_labview_render(tab):
    """Field boxes, indicator and buttons at the measured render rects (page
    coordinates = render - (OX, OY)); rendered offscreen and scanned."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    tab.resize(ct.MIN_WIDTH, ct.MIN_HEIGHT)
    buf = QBuffer(); buf.open(QIODevice.ReadWrite)
    tab.grab().save(buf, "PNG")
    import io
    im = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")
    px = im.load()

    def bbox(color, x0, y0, x1, y1):
        pts = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if px[x, y] == color]
        assert pts, (color, x0, y0, x1, y1)
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    ox, oy = ct.OX, ct.OY
    for k in range(9):
        y = 346 + 20 * k - oy
        # LabVIEW: (170) bevel box x4..78 x y..y+19 (the line at y+20 is the next row's top),
        # white interior x5..77 x y+1..y+18
        assert bbox((170, 170, 170), 3, y, 80, y + 19) == (4, y, 78, y + 19)
        assert bbox((255, 255, 255), 5, y + 1, 77, y + 18) == (5, y + 1, 77, y + 18)
    assert bbox((221, 221, 221), 315, 354 - oy, 384, 373 - oy) == (315, 354 - oy, 384, 373 - oy)   # indicator fill
    assert bbox((170, 170, 170), 313, 352 - oy, 386, 375 - oy) == (314, 353 - oy, 385, 374 - oy)   # indicator bevel
    assert bbox((255, 255, 204), 276, 353 - oy, 300, 375 - oy) == (278, 355 - oy, 295, 373 - oy)  # multiply triangle
    g = tab.save_button.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (106 - ox, 558 - oy, 85, 34)
    g = tab.revert_button.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (238 - ox, 558 - oy, 85, 34)
