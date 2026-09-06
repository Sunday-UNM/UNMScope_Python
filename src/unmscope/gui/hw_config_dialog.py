"""LouisXIV's ``HW Configuration GUI.vi`` (SPIM MAIN [45] "HW Config") as a
non-modal Qt dialog: the Cameras / Imagine Optics / Rotation Stage / Misc.
pages and the Apply / Save / Revert / Close row.

Behaviour, VI for VI (docs/hw_config.md has the full mapping):

- Initialize: the four settings groups are read from the ini
  (``load_hw_config``; the UNMScope-owned copy, never LouisXIV's file).
- Camera ring: switches which of the five camera clusters is displayed; an
  edit is written straight into that cluster (event [8] "Camera Settings:
  Value Change" -> Replace Array Subset), so switching never loses edits.
- "Property Nodes": Enabled off greys every other element of the camera
  cluster; with Enabled on, Remote greys Model / Serial Number / Image
  Transform / Sync Readout / Simulate / Binning and ungreys RemoteIP / DCAM
  Port / Cmd Port. LouisXIV's remote-node branch is compiled out under SPIM
  MAIN (conditional-disable symbol RemoteNode=False), so Save Index, Remote
  and RemoteIP are always visible and the Camera ring always enabled.
- Apply (event [4]): writes all four groups to the ini and emits ``applied``
  with the new config. LouisXIV then sends 'Acquisition Start-Stop' and
  'Reset HW' (full DAQ + camera re-initialisation) -- that reaction is the
  main window's decision, documented but not performed here.
- Save (event [7]) = Apply, then close. Revert (event [5]) re-reads the ini
  and re-displays. Close (event [3]) / the window close box discard the
  edits silently -- LouisXIV has no unsaved-changes prompt either.
- Misc. "Enter Values" (event [11]) launches ``SPIM X Galvo LUT Correction
  GUI.vi`` in LouisXIV; that editor is not ported, so the button is greyed.

The Imagine Optics, Rotation Stage and Misc. pages are ini-only editors:
nothing in Python consumes those sections yet (no HASO / deformable mirror,
PI U-651 or waveform-flag backend). They are kept so the settings file stays
complete for LouisXIV and for the future ports.

Layout: every rectangle was MEASURED with PIL edge scans on the COM-rendered
panel (VI_Diagrams/.../HW Configuration GUI/HW Configuration GUIp.png for
the Cameras page; the other three pages rendered 2026-09-05), in render
pixel coordinates; ``_r`` converts them to page coordinates. The window is
trimmed to the tab control plus the button row (486 x 490): LouisXIV's
960 x 679 panel is mostly empty, and its 'error in' / 'error out' clusters
are connector-pane terminals, not controls -- dropped (cleanup rule).
Values marked ASSUMED are not measurements.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QLabel, QLineEdit, QPushButton, QSpinBox,
    QStyle, QTabBar, QTabWidget, QWidget,
)

from unmscope.config.hw_config import (
    ANALYSIS_METHODS, BINNINGS, BINNING_LABELS, CAMERA_IDS, CAMERA_MODELS, IMAGE_TRANSFORMS,
    Z_UM_PX_SOURCES, HwConfig, copy_config, format_value, labview_path_to_windows,
    load_hw_config, save_hw_config, user_ini_path, windows_path_to_labview,
)
from unmscope.gui.widgets import bring_to_front, rect_mapper

#: MEASURED 2026-09-05: `vi.FPWinTitle` over COM. The window is titled
#: "Hardware Configuration", NOT the VI's file name, which is what we
#: had assumed. (The um/V window is "Microns per Volt Settings" and the
#: camera one "Debug Panel"; both of ours already matched.)
WINDOW_TITLE = "Hardware Configuration"
DIALOG_SIZE = (486, 490)                   # tab control 0..485 x 0..440, buttons end at y 479 (+10 px)

# Measured colours (render): pane border and unselected-tab outline (221,221,221),
# field / combo borders (170,170,170), button top-left (204,204,204) /
# bottom-right (170,170,170), button face (253,253,253) ASSUMED from the
# Camera tab's measurement, square LED buttons' border black.
PANE_BORDER = "#dddddd"
FIELD_BORDER = "#aaaaaa"
BTN_LIGHT, BTN_DARK = "#cccccc", "#aaaaaa"
BEVEL = "#d4d4d4"        # LabVIEW's classic string/numeric fields: a 4 px top-left shadow (2 rows 221 + 2 rows 204), no right/bottom edge

#: The pane's content origin: the QTabWidget sits at (0, 0), its tab bar is
#: 25 px tall and the pane border 1 px, so page (0, 0) is render (1, 26).
PAGE_OX, PAGE_OY = 1, 26
TAB_WIDTHS = (69, 98, 97, 49)              # Cameras / Imagine Optics / Rotation Stage / Misc.: dividers at x 69, 167, 264, last edge 312
TAB_HEIGHT = 25
LABEL_DY = -4                              # ASSUMED: label top = measured glyph top - 4 (Qt 9 pt ascent)

STYLE = f"""
QDialog {{ background: white; }}
QTabWidget::pane {{ border: 1px solid {PANE_BORDER}; background: white; }}
QTabBar::tab {{ border: 1px solid {PANE_BORDER}; border-right: none; border-bottom: none; background: white; padding: 0; }}
QTabBar::tab:last {{ border-right: 1px solid {PANE_BORDER}; }}
QTabBar::tab:selected {{ border-bottom: 1px solid white; }}
QWidget#hwPage {{ background: white; }}
QLineEdit, QComboBox, QSpinBox {{ border: 1px solid {FIELD_BORDER}; background: white; padding: 0 2px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: #777777; border-color: {PANE_BORDER}; }}
QLineEdit#hwBevel, QSpinBox#hwBevel {{ border: none; border-top: 4px solid {BEVEL}; border-left: 4px solid {BEVEL}; }}
QComboBox::drop-down {{ width: 16px; border-left: 1px solid {FIELD_BORDER}; }}
QCheckBox::indicator {{ width: 13px; height: 13px; }}
QPushButton#hwButton {{ border: 1px solid {BTN_DARK}; border-top-color: {BTN_LIGHT}; border-left-color: {BTN_LIGHT};
                        background: #fdfdfd; }}
QPushButton#hwButton:pressed {{ background: #e5e5e5; }}
QPushButton#hwButton:disabled {{ color: #999999; }}
QPushButton#hwSquare {{ border: 1px solid black; background: white; }}
QPushButton#hwSquare:checked {{ background: #40c040; }}
"""


#: Measured render rect -> page rect.
_r = rect_mapper(PAGE_OX, PAGE_OY)


class _FixedTabBar(QTabBar):
    """Tabs at LouisXIV's measured widths (the offscreen font would otherwise
    decide them)."""

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802 (Qt API)
        return QSize(TAB_WIDTHS[index] if index < len(TAB_WIDTHS) else 60, TAB_HEIGHT)


class _FloatEdit(QLineEdit):
    """A DBL numeric shown the way LabVIEW shows it ('72', '2.5'); commits on
    editing finished."""
    value_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.editingFinished.connect(self._commit)

    def value(self) -> float:
        return self._value

    def set_value(self, v: float) -> None:
        self._value = float(v)
        self.setText(format_value(self._value, "float"))

    def _commit(self) -> None:
        try:
            v = float(self.text().strip())
        except ValueError:
            self.set_value(self._value)          # LabVIEW keeps the old value on bad input
            return
        if v != self._value:
            self._value = v
            self.value_changed.emit(v)
        self.setText(format_value(v, "float"))


class HwConfigDialog(QDialog):
    """See the module docstring. ``applied`` carries the HwConfig that was
    just written (emitted by Apply and by Save)."""

    applied = Signal(object)

    def __init__(self, ini_path: str | Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ini_path = user_ini_path(ini_path)
        self.config: HwConfig = load_hw_config(self.ini_path)
        self._loading = False
        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(False)
        self.setStyleSheet(STYLE)
        self.setFixedSize(*DIALOG_SIZE)
        self._build()
        self.refresh()

    # -- construction -----------------------------------------------------------
    def _page(self, name: str) -> QWidget:
        w = QWidget()
        w.setObjectName("hwPage")
        self.tabs.addTab(w, name)
        return w

    @staticmethod
    def _label(parent, text, rect, *, wrap=False) -> QLabel:
        lab = QLabel(text, parent)
        x, y, w, h = _r(*rect)
        lab.setGeometry(x, y + LABEL_DY, w, h)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignTop if wrap else Qt.AlignLeft | Qt.AlignVCenter)
        lab.setWordWrap(wrap)
        return lab

    @staticmethod
    def _check(parent, text, rect) -> QCheckBox:
        c = QCheckBox(text, parent)
        c.setGeometry(*_r(*rect))
        return c

    @staticmethod
    def _combo(parent, items, rect) -> QComboBox:
        c = QComboBox(parent)
        c.addItems(list(items))
        c.setGeometry(*_r(*rect))
        return c

    @staticmethod
    def _text(parent, rect, *, bevel=False) -> QLineEdit:
        e = QLineEdit(parent)
        e.setGeometry(*_r(*rect))
        if bevel:
            e.setObjectName("hwBevel")
        return e

    @staticmethod
    def _int(parent, rect, lo=-2_147_483_648, hi=2_147_483_647, *, arrows=False, bevel=False) -> QSpinBox:
        s = QSpinBox(parent)
        s.setGeometry(*_r(*rect))
        s.setRange(lo, hi)
        s.setKeyboardTracking(False)
        if not arrows:
            s.setButtonSymbols(QSpinBox.NoButtons)    # LabVIEW draws no arrows on these
        if bevel:
            s.setObjectName("hwBevel")
        return s

    @staticmethod
    def _float(parent, rect) -> _FloatEdit:
        f = _FloatEdit(parent)
        f.setGeometry(*_r(*rect))
        return f

    def _button(self, parent, text, rect, slot=None, *, name="hwButton") -> QPushButton:
        b = QPushButton(text, parent)
        b.setObjectName(name)
        b.setAutoDefault(False)        # Enter in a field must not press a button (LabVIEW: Enter only commits)
        b.setGeometry(*_r(*rect) if parent is not self else rect)
        if slot is not None:
            b.clicked.connect(slot)
        return b

    def _browse(self, parent, rect, on_pick: Callable[[], None]) -> QPushButton:
        b = QPushButton(parent)
        b.setObjectName("hwButton")
        b.setAutoDefault(False)
        b.setGeometry(*_r(*rect))
        b.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        b.clicked.connect(on_pick)
        return b

    def _build(self) -> None:
        self.tabs = QTabWidget(self)
        self.tabs.setTabBar(_FixedTabBar())
        self.tabs.setGeometry(0, 0, 486, 441)
        self.tabs.setDocumentMode(False)
        self._build_cameras(self._page("Cameras"))
        self._build_imagine_optics(self._page("Imagine Optics"))
        self._build_rotation_stage(self._page("Rotation Stage"))
        self._build_misc(self._page("Misc."))
        self.tabs.currentChanged.connect(lambda _i: self.refresh())    # [2] Tab Control -> Display Configs + Property Nodes

        self.apply_btn = self._button(self, "Apply", (82, 446, 85, 34), self.apply)
        self.save_btn = self._button(self, "Save", (178, 446, 85, 34), self.save)
        self.revert_btn = self._button(self, "Revert", (274, 446, 85, 34), self.revert)
        self.close_btn = self._button(self, "Close", (370, 446, 85, 34), self.reject)

    def _build_cameras(self, page: QWidget) -> None:
        self._label(page, "Camera", (22, 82, 60, 16))
        self.camera_combo = self._combo(page, CAMERA_IDS, (21, 94, 135, 21))
        self.camera_combo.currentIndexChanged.connect(lambda _i: self.refresh())   # [1] Camera -> Display Configs + Property Nodes
        self._label(page, "*** Note: Enabling and Disabling cameras requires SW restart ***",
                                (246, 117, 200, 34), wrap=True)

        self.cam_enabled = self._check(page, "Enabled", (27, 132, 90, 13))
        self._label(page, "Model", (28, 151, 60, 16))
        self.cam_model = self._combo(page, CAMERA_MODELS, (27, 164, 69, 21))
        self._label(page, "Serial Number", (30, 188, 80, 16))
        self.cam_serial = self._text(page, (27, 201, 72, 24), bevel=True)     # +1 px: the bevel shadow runs 1 px past the fill
        self._label(page, "Save Index", (28, 229, 70, 16))
        self.cam_save_index = self._int(page, (27, 242, 54, 22), bevel=True)
        self.cam_sync_readout = self._check(page, "Sync Readout", (27, 266, 100, 13))
        self._label(page, "Image Transform", (28, 283, 95, 16))
        self.cam_image_transform = self._combo(page, IMAGE_TRANSFORMS, (27, 296, 80, 21))
        self.cam_simulate = self._check(page, "Simulate", (27, 327, 90, 13))

        self.cam_remote = self._check(page, "Remote", (149, 155, 70, 13))
        self._label(page, "RemoteIP", (150, 172, 70, 16))
        self.cam_remote_ip = self._text(page, (149, 185, 84, 24), bevel=True)
        self._label(page, "DCAM Port", (150, 213, 70, 16))
        self.cam_dcam_port = self._int(page, (149, 226, 54, 22), bevel=True)
        self._label(page, "Cmd Port", (150, 252, 70, 16))
        self.cam_cmd_port = self._int(page, (149, 265, 54, 22), bevel=True)
        self._label(page, "Binning", (150, 292, 50, 16))
        self.cam_binning = self._combo(page, BINNING_LABELS, (149, 306, 48, 23))

        # widget -> cluster attribute; every edit lands in config.cameras[current]
        self._cam_bind = {
            "enabled": self.cam_enabled, "simulate": self.cam_simulate, "model": self.cam_model,
            "serial_number": self.cam_serial, "save_index": self.cam_save_index,
            "sync_readout": self.cam_sync_readout, "image_transform": self.cam_image_transform,
            "remote": self.cam_remote, "remote_ip": self.cam_remote_ip, "dcam_port": self.cam_dcam_port,
            "cmd_port": self.cam_cmd_port, "binning": self.cam_binning,
        }
        for attr, w in self._cam_bind.items():
            self._connect(w, lambda v, a=attr: self._on_camera_edit(a, v))

    def _build_imagine_optics(self, page: QWidget) -> None:
        self.io_enable = self._check(page, "Enable", (11, 36, 70, 13))
        self.io_simulate = self._check(page, "Simulate", (11, 51, 70, 13))
        self.io_n_pts = self._int(page, (11, 64, 58, 21), arrows=True)
        self._label(page, "# Pts (Default)", (69, 69, 90, 16))
        self.io_default_camera = self._combo(page, CAMERA_IDS, (11, 84, 135, 21))
        self._label(page, "Default Camera", (148, 90, 90, 16))
        self._io_paths: dict[str, QLineEdit] = {}
        rows = (("wavefront_corrector_setup_file", "Wavefront Corrector Setup File", 112, "*.dat"),
                ("haso_config_file", "HasoConfigFile", 152, "*.dat"),
                ("correction_interaction_matrix_file", "Correction Interaction Matrix File", 192, "*.aoc"),
                ("default_wavefront_positions_file", "Default Wavefront Positions File", 232, "*.wcs"),
                ("flat_wavefront_positions_file", "Flat Wavefront Positions File", 272, "*.wcs"))
        for attr, label, ly, pattern in rows:
            self._label(page, label, (15, ly, 250, 16))
            e = self._text(page, (15, ly + 14, 420, 23))
            self._browse(page, (438, ly + 15, 31, 17),
                         lambda a=attr, pat=pattern, e=e: self._pick_file(e, pat))
            self._io_paths[attr] = e
        self._label(page, "Sleep after command apply (ms)", (17, 312, 180, 16))
        self.io_sleep_ms = self._int(page, (15, 328, 58, 20), arrows=True)
        self._label(page, "Default Analyis Method", (11, 356, 150, 16))
        self.io_analysis = self._combo(page, ANALYSIS_METHODS, (11, 371, 192, 21))
        self._label(page, "Matlab Script Directory", (12, 395, 140, 16))
        self.io_matlab_dir = self._text(page, (11, 409, 420, 23))
        self._browse(page, (434, 410, 31, 17), lambda: self._pick_dir(self.io_matlab_dir))

        self._io_bind = {"enable": self.io_enable, "simulate": self.io_simulate, "n_pts_default": self.io_n_pts,
                         "default_camera": self.io_default_camera, "default_analysis_method": self.io_analysis,
                         "matlab_script_directory": self.io_matlab_dir}
        for attr, w in self._io_bind.items():
            self._connect(w, lambda v, a=attr: self._store("imagine_optics", a, v))
        self._ctl_bind = dict(self._io_paths)
        self._ctl_bind["sleep_after_command_apply_ms"] = self.io_sleep_ms
        for attr, w in self._ctl_bind.items():
            self._connect(w, lambda v, a=attr: self._store("controller", a, v))

    def _build_rotation_stage(self, page: QWidget) -> None:
        self._label(page, "Rotation Stage (PI U651) Settings", (25, 48, 220, 16))   # the one visible cluster caption
        self.rs_enable = self._check(page, "Enable?", (28, 70, 70, 13))
        self.rs_simulate = self._check(page, "Simulate", (28, 85, 70, 13))
        self.rs_serial = self._text(page, (28, 98, 147, 21))
        self._label(page, "Serial Number", (175, 103, 90, 16))
        self.rs_speed = self._float(page, (28, 119, 39, 20))
        self._label(page, "Speed (deg/s)", (69, 124, 100, 16))
        self.rs_settling = self._float(page, (28, 139, 39, 20))
        self._label(page, "Settling Time (ms)", (69, 144, 120, 16))
        self._rs_bind = {"enable": self.rs_enable, "simulate": self.rs_simulate, "serial_number": self.rs_serial,
                         "speed_deg_s": self.rs_speed, "settling_time_ms": self.rs_settling}
        for attr, w in self._rs_bind.items():
            self._connect(w, lambda v, a=attr: self._store("rotation_stage", a, v))

    def _build_misc(self, page: QWidget) -> None:
        self.misc_z_source = self._combo(page, Z_UM_PX_SOURCES, (34, 52, 116, 21))
        self._label(page, "Z um/px source", (151, 57, 100, 16))
        squares = (("disable_xg_zg_zp_calibrations", "Disable Xg-Zg-Zp Calibrations", 73),
                   ("enable_x_galvo_correction_lut", "Enable X Galvo Correction LUT", 97),
                   ("enable_z_piezo_2", "Enable Z Piezo 2 (Dither Chnl.)", 121),
                   ("use_alternating_galvo_channels", "Use Z Galvo and Dither as Alternating Galvo Channels", 145))
        self._misc_bind: dict[str, QWidget] = {"z_um_px_source": self.misc_z_source}
        for attr, label, y in squares:
            b = self._button(page, "", (34, y, 25, 24), name="hwSquare")
            b.setCheckable(True)
            self._label(page, label, (61, y + 8, 300, 16))
            self._misc_bind[attr] = b
        # [11] "Enter X Galvo Linearize LUT Values" -> SPIM X Galvo LUT Correction GUI.vi: not ported
        self.enter_values_btn = self._button(page, "Enter Values", (214, 111, 85, 34))
        self.enter_values_btn.setEnabled(False)
        self.enter_values_btn.setToolTip("X Galvo LUT editor (SPIM X Galvo LUT Correction GUI.vi) not ported yet")
        numerics = (("galvo1_alternating_first_v", "Galvo 1 Alternating First (V)", 169),
                    ("galvo1_alternating_second_v", "Galvo 1 Alternating Second (V)", 189),
                    ("galvo2_alternating_first_v", "Galvo 2 Alternating First (V)", 209),
                    ("galvo2_alternating_second_v", "Galvo 2 Alternating Second (V)", 229))
        for attr, label, y in numerics:
            f = self._float(page, (34, y, 39, 20))
            self._label(page, label, (75, y + 5, 200, 16))
            self._misc_bind[attr] = f
        for attr, w in self._misc_bind.items():
            self._connect(w, lambda v, a=attr: self._store("misc", a, v))

    # -- binding helpers ----------------------------------------------------------
    @staticmethod
    def _connect(w: QWidget, slot: Callable[[object], None]) -> None:
        if isinstance(w, QCheckBox):
            w.toggled.connect(slot)
        elif isinstance(w, QPushButton):
            w.toggled.connect(slot)
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(slot)
        elif isinstance(w, QSpinBox):
            w.valueChanged.connect(slot)
        elif isinstance(w, _FloatEdit):
            w.value_changed.connect(slot)
        elif isinstance(w, QLineEdit):
            w.textEdited.connect(slot)
        else:
            raise TypeError(type(w))

    @staticmethod
    def _to_widget(w: QWidget, value, attr: str) -> None:
        if isinstance(w, (QCheckBox, QPushButton)):
            w.setChecked(bool(value))
        elif isinstance(w, QComboBox):
            idx = BINNINGS.index(value) if attr == "binning" and value in BINNINGS else int(value)
            w.setCurrentIndex(max(0, min(w.count() - 1, idx)))
        elif isinstance(w, QSpinBox):
            w.setValue(int(value))
        elif isinstance(w, _FloatEdit):
            w.set_value(float(value))
        elif isinstance(w, QLineEdit):
            w.setText(labview_path_to_windows(str(value)) if _is_path(attr) else str(value))

    @staticmethod
    def _from_widget(value, attr: str):
        if attr == "binning":
            return BINNINGS[int(value)]
        if _is_path(attr):
            return windows_path_to_labview(str(value))
        return value

    def _cluster(self, group: str):
        """Resolved at call time: Revert replaces ``self.config``."""
        if group == "controller":
            return self.config.imagine_optics.controller
        return getattr(self.config, group)

    def _store(self, group: str, attr: str, value) -> None:
        if not self._loading:
            setattr(self._cluster(group), attr, self._from_widget(value, attr))

    def _on_camera_edit(self, attr: str, value) -> None:
        if self._loading:
            return
        setattr(self.config.cameras[self.camera_index], attr, self._from_widget(value, attr))
        self._property_nodes()       # [8] Camera Settings: Value Change -> Property Nodes

    @property
    def camera_index(self) -> int:
        return self.camera_combo.currentIndex()

    # -- Display Configs / Property Nodes ------------------------------------------
    def refresh(self) -> None:
        """LouisXIV's 'Display Configs' + 'Property Nodes': show the stored
        clusters in the widgets and apply the greying rules."""
        self._loading = True
        try:
            cam = self.config.cameras[self.camera_index]
            for attr, w in self._cam_bind.items():
                self._to_widget(w, getattr(cam, attr), attr)
            io = self.config.imagine_optics
            for attr, w in self._io_bind.items():
                self._to_widget(w, getattr(io, attr), attr)
            for attr, w in self._ctl_bind.items():
                self._to_widget(w, getattr(io.controller, attr), attr)
            for attr, w in self._rs_bind.items():
                self._to_widget(w, getattr(self.config.rotation_stage, attr), attr)
            for attr, w in self._misc_bind.items():
                self._to_widget(w, getattr(self.config.misc, attr), attr)
        finally:
            self._loading = False
        self._property_nodes()

    def _property_nodes(self) -> None:
        """Frame 0 of the 'Property Nodes' state for the Cameras page
        (SPIM MAIN build: remote node? = FALSE)."""
        cam = self.config.cameras[self.camera_index]
        enabled, remote = cam.enabled, cam.remote
        for attr, w in self._cam_bind.items():
            if attr == "enabled":
                continue
            if not enabled:
                w.setEnabled(False)                               # 'disable all but Enabled switch'
            elif attr in ("remote_ip", "dcam_port", "cmd_port"):
                w.setEnabled(remote)
            elif attr in ("save_index", "remote"):
                w.setEnabled(True)
            else:                                                  # model, serial, transform, sync, simulate, binning
                w.setEnabled(not remote)

    # -- the button row -----------------------------------------------------------
    def apply(self) -> bool:
        """[4] Apply Configs: write the ini copy, then tell the window."""
        try:
            save_hw_config(self.config, self.ini_path)
        except OSError as e:               # LouisXIV: Simple Error Handler dialog
            QMessageBox.warning(self, "HW Config", f"Could not write {self.ini_path}:\n{e}")
            return False
        self.applied.emit(copy_config(self.config))
        return True

    def save(self) -> None:
        """[7] Save = Apply Configs + Exit."""
        if self.apply():
            self.accept()

    def revert(self) -> None:
        """[5] Revert Configs: re-read every group from the ini, re-display."""
        self.config = load_hw_config(self.ini_path)
        self.refresh()

    # -- file pickers -------------------------------------------------------------
    def _pick_file(self, edit: QLineEdit, pattern: str) -> None:
        start = edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Select file", start, f"{pattern};;All files (*)")
        if path:
            edit.setText(str(Path(path)))
            edit.textEdited.emit(edit.text())

    def _pick_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose directory containing Matlab scripts (.m files)",
                                                edit.text() or str(Path.home()))
        if path:
            edit.setText(str(Path(path)))
            edit.textEdited.emit(edit.text())


def _is_path(attr: str) -> bool:
    return attr.endswith(("_file", "_directory"))


def show_hw_config_dialog(parent: QWidget | None = None, ini_path: str | Path | None = None,
                          existing: HwConfigDialog | None = None) -> HwConfigDialog:
    """Open the panel the way SPIM MAIN [45] does (asynchronous, non-modal),
    re-raising an already open one instead of opening a second."""
    dlg = existing
    if dlg is None or not dlg.isVisible():
        dlg = HwConfigDialog(ini_path, parent)
    bring_to_front(dlg)
    return dlg
