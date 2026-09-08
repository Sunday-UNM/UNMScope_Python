"""LouisXIV's **Save Image** dialog (`File IO/OME Save Image Dialog.vi`).

Asked once per session, before the first acquisition. The port used to put
up a bare Qt save-file dialog and take the folder from whatever file name
was typed; the real thing is a form, and the save directory is *built* from
its fields rather than picked. The user's block diagram says so in a
comment on the VI itself:

    From Reto: \\username\\celltype\\labeling\\YYMMDD\\cell[n]\\location[n]\\n.tif

so the path is

    <Root Save Directory>\\<User Name>\\<Cell Type>\\<Cell Labeling>\\<YYMMDD>\\<Experiment>

with **Date** and **Experiment** computed, not typed: the date is today in
YYMMDD, and Experiment is the next free ``Cell<N>`` inside the date folder
(``Find Next Experiment Folder Number.vi``, already ported as
``MainWindow.next_experiment_folder``). Output Path shows the result live
and is created on OK if it does not exist.

The **Channels** tab of the VI is not reproduced: it is filled in from the
waveform globals and the camera rather than typed, and nothing in this port
consumes it yet. Its absence is visible rather than silent -- the tab is
there and says so.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

#: Characters that cannot appear in a Windows path element. The fields become
#: folder names, so a typed "/" would silently create a level of nesting.
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean_path_element(text: str) -> str:
    """One typed field -> one safe folder name. Empty stays empty, and the
    caller decides what an empty field means (here: that level is skipped,
    the way LouisXIV's own path build does with a blank control)."""
    return _BAD.sub("_", (text or "").strip()).rstrip(". ")


def today_yymmdd(today: _dt.date | None = None) -> str:
    return (today or _dt.date.today()).strftime("%y%m%d")


class SaveImageDialog(QDialog):
    """The form. ``result_path()`` is the composed Output Path after OK."""

    def __init__(self, parent=None, *, root: str = "", user_name: str = "", cell_type: str = "",
                 cell_labeling: str = "", description: str = "",
                 next_experiment: Callable[[Path], str] | None = None,
                 today: _dt.date | None = None):
        super().__init__(parent)
        self.setWindowTitle("Save Image")
        self.setMinimumWidth(560)
        #: How the Experiment folder is chosen. Injected so this dialog does
        #: not import MainWindow, and so a test can pin it.
        self._next_experiment = next_experiment or (lambda p: "Cell1")
        self._date = today_yymmdd(today)

        root_lay = QVBoxLayout(self)
        root_lay.addWidget(QLabel("The save directory is automatically created using the "
                                  "following fields:"))

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.root_dir = QLineEdit(root)
        browse = QPushButton("...")
        browse.setFixedWidth(32)
        browse.setToolTip("Choose the root save directory")
        browse.clicked.connect(self._on_browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.root_dir, stretch=1)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        grid.addWidget(QLabel("Root Save Directory"), 0, 0, 1, 4)
        grid.addWidget(holder, 1, 0, 1, 4)

        self.user_name = QLineEdit(user_name)
        self.cell_type = QLineEdit(cell_type)
        self.cell_labeling = QLineEdit(cell_labeling)
        grid.addWidget(QLabel("User Name"), 2, 0)
        grid.addWidget(QLabel("Cell Type"), 2, 2)
        grid.addWidget(self.user_name, 3, 0, 1, 2)
        grid.addWidget(self.cell_type, 3, 2, 1, 2)

        self.date_edit = QLineEdit(self._date)
        self.date_edit.setReadOnly(True)          # computed, as on the real panel
        grid.addWidget(QLabel("Cell Labeling"), 4, 0)
        grid.addWidget(QLabel("Date (YYMMDD)"), 4, 2)
        grid.addWidget(self.cell_labeling, 5, 0, 1, 2)
        grid.addWidget(self.date_edit, 5, 2, 1, 2)

        self.experiment = QLineEdit()
        self.experiment.setReadOnly(True)         # next free Cell<N>, computed
        grid.addWidget(QLabel("Experiment"), 6, 0)
        grid.addWidget(self.experiment, 7, 0)

        self.output_path = QPlainTextEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setFixedHeight(48)
        grid.addWidget(QLabel("Output Path"), 8, 0, 1, 4)
        grid.addWidget(self.output_path, 9, 0, 1, 4)
        note = QLabel("(Path will be created if it doesn't exist)")
        note.setStyleSheet("color: #006000;")
        grid.addWidget(note, 10, 0, 1, 4)
        root_lay.addLayout(grid)

        tabs = QTabWidget()
        desc_tab = QWidget()
        desc_lay = QVBoxLayout(desc_tab)
        desc_lay.addWidget(QLabel("Experiment Description"))
        self.description = QPlainTextEdit(description)
        desc_lay.addWidget(self.description)
        tabs.addTab(desc_tab, "Experiment Description")

        chan_tab = QWidget()
        chan_lay = QVBoxLayout(chan_tab)
        chan_lay.addWidget(QLabel(
            "Not ported. LouisXIV fills this tab in from the waveform settings and the\n"
            "camera (Fluor, ND Filter, emission and excitation wavelength per channel);\n"
            "nothing in UNMScope reads it yet."))
        chan_lay.addStretch(1)
        tabs.addTab(chan_tab, "Channels")
        tabs.setTabEnabled(1, False)
        root_lay.addWidget(tabs, stretch=1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root_lay.addWidget(self.buttons)

        for w in (self.root_dir, self.user_name, self.cell_type, self.cell_labeling):
            w.textChanged.connect(self._refresh_output_path)
        self._refresh_output_path()

    # -- the path ----------------------------------------------------------
    def _parts(self) -> list[str]:
        return [p for p in (clean_path_element(self.user_name.text()),
                            clean_path_element(self.cell_type.text()),
                            clean_path_element(self.cell_labeling.text()),
                            self._date) if p]

    def date_folder(self) -> Path | None:
        """Everything above the Experiment folder, or None with no root."""
        root = self.root_dir.text().strip()
        if not root:
            return None
        return Path(root).joinpath(*self._parts())

    def result_path(self) -> Path | None:
        """The full Output Path: the date folder plus the Experiment folder."""
        base = self.date_folder()
        if base is None:
            return None
        return base / self.experiment.text() if self.experiment.text() else base

    def _refresh_output_path(self) -> None:
        base = self.date_folder()
        if base is None:
            self.experiment.setText("")
            self.output_path.setPlainText("")
        else:
            # The next free Cell<N> is decided by what is already in the date
            # folder, so it has to be recomputed whenever any field above it
            # changes -- a different cell type is a different folder.
            self.experiment.setText(self._next_experiment(base))
            self.output_path.setPlainText(str(self.result_path()))
        ok = self.buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(base is not None)

    def _on_browse(self) -> None:
        start = self.root_dir.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Root save directory", start)
        if chosen:
            self.root_dir.setText(chosen)

    # -- what the caller wants back ----------------------------------------
    def values(self) -> dict:
        return {
            "root": self.root_dir.text().strip(),
            "user_name": self.user_name.text().strip(),
            "cell_type": self.cell_type.text().strip(),
            "cell_labeling": self.cell_labeling.text().strip(),
            "date": self._date,
            "experiment": self.experiment.text(),
            "description": self.description.toPlainText(),
            "path": self.result_path(),
        }
