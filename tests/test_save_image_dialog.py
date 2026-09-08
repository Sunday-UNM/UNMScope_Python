"""LouisXIV's Save Image dialog (`File IO/OME Save Image Dialog.vi`).

The user's live panel, 2026-09-07: Root Save Directory, User Name, Cell
Type, Cell Labeling, a computed Date (YYMMDD) and Experiment, a computed
Output Path, and an Experiment Description tab. The VI's own block diagram
carries the path rule as a comment:

    From Reto: \\username\\celltype\\labeling\\YYMMDD\\cell[n]\\location[n]\\n.tif

The port had a bare save-file dialog instead, so User Name / Cell Type /
Cell Labeling / Experiment Description were never collected and went into
AcqInfo.txt empty.
"""
import datetime as dt
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from unmscope.gui.save_image_dialog import (  # noqa: E402
    SaveImageDialog, clean_path_element, today_yymmdd,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _dlg(tmp_path, **kw):
    kw.setdefault("root", str(tmp_path))
    kw.setdefault("user_name", "CHITRA")
    kw.setdefault("cell_type", "CELEGAN")
    kw.setdefault("cell_labeling", "SINGLESTAIN")
    kw.setdefault("today", dt.date(2026, 9, 7))
    kw.setdefault("next_experiment", lambda base: "Cell1")
    return SaveImageDialog(**kw)


def test_the_output_path_is_built_the_way_the_vi_says(app, tmp_path):
    d = _dlg(tmp_path)
    assert d.date_edit.text() == "260907"
    assert d.experiment.text() == "Cell1"
    assert d.result_path() == tmp_path / "CHITRA" / "CELEGAN" / "SINGLESTAIN" / "260907" / "Cell1"
    assert d.output_path.toPlainText() == str(d.result_path())


def test_the_path_follows_the_fields_as_they_are_typed(app, tmp_path):
    d = _dlg(tmp_path)
    d.cell_type.setText("HELA")
    assert d.result_path() == tmp_path / "CHITRA" / "HELA" / "SINGLESTAIN" / "260907" / "Cell1"
    d.user_name.setText("")
    # an empty field drops that level rather than leaving an empty folder name
    assert d.result_path() == tmp_path / "HELA" / "SINGLESTAIN" / "260907" / "Cell1"


def test_the_computed_fields_cannot_be_typed_into(app, tmp_path):
    """Date and Experiment are indicators on the real panel, not controls."""
    d = _dlg(tmp_path)
    assert d.date_edit.isReadOnly()
    assert d.experiment.isReadOnly()
    assert d.output_path.isReadOnly()


def test_the_experiment_folder_is_recomputed_per_directory(app, tmp_path):
    """Cell<N> depends on what is already in the date folder, so changing a
    field above it has to re-ask -- a different cell type is a different
    folder with its own numbering."""
    seen = []

    def next_experiment(base):
        seen.append(base)
        return f"Cell{len(seen)}"

    d = _dlg(tmp_path, next_experiment=next_experiment)
    first = len(seen)
    d.cell_labeling.setText("DOUBLESTAIN")
    assert len(seen) > first
    assert seen[-1] == tmp_path / "CHITRA" / "CELEGAN" / "DOUBLESTAIN" / "260907"


def test_a_typed_separator_cannot_create_a_folder_level(app, tmp_path):
    d = _dlg(tmp_path)
    d.cell_type.setText("a/b\\c")
    assert d.result_path() == tmp_path / "CHITRA" / "a_b_c" / "SINGLESTAIN" / "260907" / "Cell1"


def test_ok_is_refused_without_a_root_directory(app, tmp_path):
    d = _dlg(tmp_path, root="")
    assert not d.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert d.result_path() is None
    d.root_dir.setText(str(tmp_path))
    assert d.buttons.button(QDialogButtonBox.Ok).isEnabled()


def test_values_carries_everything_the_caller_needs(app, tmp_path):
    d = _dlg(tmp_path)
    d.description.setPlainText("0.9NA secondary, snouty tertiary")
    v = d.values()
    assert v["user_name"] == "CHITRA" and v["cell_type"] == "CELEGAN"
    assert v["cell_labeling"] == "SINGLESTAIN" and v["date"] == "260907"
    assert v["experiment"] == "Cell1"
    assert v["description"] == "0.9NA secondary, snouty tertiary"
    assert v["path"] == d.result_path()


def test_the_channels_tab_is_present_but_honestly_disabled(app, tmp_path):
    """LouisXIV fills it from the waveform settings; nothing here reads it
    yet, so it is greyed rather than faked (CLAUDE.md)."""
    from PySide6.QtWidgets import QTabWidget
    d = _dlg(tmp_path)
    tab = d.findChild(QTabWidget)
    assert tab.tabText(1) == "Channels"
    assert not tab.isTabEnabled(1)


def test_helpers(app):
    assert clean_path_element("  a b  ") == "a b"
    assert clean_path_element('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert clean_path_element("") == ""
    assert today_yymmdd(dt.date(2026, 1, 2)) == "260102"
