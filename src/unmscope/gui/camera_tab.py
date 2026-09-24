"""The Camera tab of SPIM MAIN.vi.

Layout: every rectangle below was MEASURED on the live LouisXIV front panel
(2026-09-05, PIL edge/flood scans of a capture of `SPIM MAIN V4.107.100`;
full-capture pixel coordinates, converted to tab-page coordinates by the
page origin ``OX, OY``). Values marked ASSUMED are not measurements.

Behaviour, VI for VI (H:\\UNM_Lightsheet\\VI_Diagrams):
- ROI Left/Right/Top/Bottom are 1-based inclusive; every change goes through
  ``DCAM - Coerce ROI`` + ``DCAM - Coerce ROI size`` (unmscope.hardware.roi)
  and what the driver would accept is written back into the fields.
- '# of pixels' X/Y edits resize the ROI about its centre (``HHMI - Adjust
  ROI based upon change in number of pixels``).
- FOV = pixels * Camera Image Pixel size (``Camera Image Pixel sizes.vi``).
- Sensor Mode drives ``DCAM - Set Sensor Mode``.
- The buttons follow SPIM MAIN.vi's event cases [5], [9], [21], [22] and the
  ROI handler [73] (hidden-frames diagram export); see roi.py.
- Settings are pushed to the camera when it is idle, and again at scan start
  (``apply_to_camera``, LouisXIV's 'DCAM - Set parameters' at Acquire), since
  DCAM cannot change the subarray while a sequence is armed.

Removed on the user's cleanup call (2026-09-05): SubROIs / Full ROI, Dual
View mode, Split pix # (LouisXIV shows them greyed in Normal Scan anyway).
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLabel, QLineEdit, QPushButton, QSpinBox,
    QWidget,
)

from unmscope.hardware.camera import Camera
from unmscope.hardware.roi import (
    Roi, adjust_roi_for_pixels, center_roi, center_roi_at, centered_roi, coerce_roi, fov_um,
    full_roi,
)
from unmscope.gui.widgets import rect_mapper, set_bold

#: Tab-page origin in the full-panel capture. The tab body's outer border is
#: at x=8 and the strip top at y=88 (+-1 px); (9, 110) is what makes every box
#: land at the same offset from the tab widget frame as on the live panel
#: (measured: Camera Settings box 15 px right of / 83 px below the frame).
OX, OY = 9, 110

FIELD_GREY = "#f0f0f0"     # measured (240,240,240): read-back fields
PANEL_BG = "#fafafa"       # measured (250,250,250): tab page / Camera Settings interior
BOX_BORDER = "#000000"     # measured (0,0,0): every box border on the live tab is 1 px black


#: Measured full-capture rect -> page rect.
_rect = rect_mapper(OX, OY)


class CameraTab(QWidget):
    """See the module docstring. ``get_camera`` returns the connected
    Camera or None; ``pixel_size_um(binning)`` comes from the calibration.

    ``magnification``/``camera_pixel_um`` seed the Detection Optics box
    (below the real panel's own content -- see ``_build``'s comment there);
    ``set_detection(magnification, camera_pixel_um)``, when given, is called
    when the user clicks Set Optics."""

    roi_changed = Signal(object)

    def __init__(self, get_camera: Callable[[], Camera | None],
                 pixel_size_um: Callable[[int], float], log: Callable[[str], None],
                 magnification: float = 30.0, camera_pixel_um: float = 6.5,
                 set_detection: Callable[[float, float], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._get_camera = get_camera
        self._pixel_size_um = pixel_size_um
        self._log = log
        self._initial_magnification = magnification
        self._initial_camera_pixel_um = camera_pixel_um
        self._set_detection = set_detection or (lambda mag, px: None)
        self._sensor = (Camera.SENSOR_WIDTH, Camera.SENSOR_HEIGHT)
        self._units = (4, 4, 4, 4)
        self._roi = full_roi(*self._sensor)
        self._updating = False
        self.pending_apply = False
        self.setStyleSheet(f"CameraTab {{ background: {PANEL_BG}; }}")
        # The live page is 810 px tall (y 111..921); ours is shorter by the
        # Hardware Connection bar, so the host wraps this in a scroll area.
        self.setMinimumSize(392, 775)      # lowest widget: ROI center box bottom at page y 763
        self._build()
        self._show_roi(self._roi)

    # -- construction -----------------------------------------------------------
    def _label(self, text: str, rect, *, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter) -> QLabel:
        lab = QLabel(text, self)
        lab.setGeometry(*rect)
        lab.setAlignment(align)
        if bold:
            set_bold(lab)
        return lab

    def _box(self, rect, *, white: bool) -> QFrame:
        box = QFrame(self)
        box.setObjectName("camBox")
        box.setGeometry(*rect)
        bg = "#ffffff" if white else PANEL_BG
        box.setStyleSheet(f"QFrame#camBox {{ border: 1px solid {BOX_BORDER}; background: {bg}; }}")
        box.lower()
        return box

    def _readback(self, rect, text="") -> QLineEdit:
        f = QLineEdit(text, self)
        f.setGeometry(*rect)
        f.setReadOnly(True)
        f.setFocusPolicy(Qt.NoFocus)
        f.setStyleSheet(f"background: {FIELD_GREY};")
        return f

    def _spin(self, rect, lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox(self)
        s.setGeometry(*rect)
        s.setRange(lo, hi)
        s.setValue(value)
        s.setKeyboardTracking(False)
        # LabVIEW's increment arrows are an 8 px strip; Qt's are ~16 px and
        # clip a 4-digit value in these measured 53-56 px fields, so the
        # numbers are typed / wheel-edited instead (up/down keys still work).
        s.setButtonSymbols(QSpinBox.NoButtons)
        s.setAlignment(Qt.AlignRight)
        return s

    def _button(self, text: str, rect, slot) -> QPushButton:
        b = QPushButton(text, self)
        b.setGeometry(*rect)
        b.clicked.connect(slot)
        return b

    def _build(self) -> None:
        # camera name indicator ("Orca4.0" on the live panel)
        self.camera_name_field = self._readback(_rect(23, 130, 129, 17))

        # Camera Settings box (interior is the panel colour, not white)
        self._label("Camera Settings", _rect(23, 151, 130, 18), bold=True)
        self._box(_rect(23, 171, 216, 366), white=False)
        self._label("Exposure (ms)", _rect(30, 198, 90, 18))
        self.exposure_spin = QDoubleSpinBox(self)
        self.exposure_spin.setGeometry(*_rect(125, 198, 106, 18))
        self.exposure_spin.setRange(0.1, 10000.0)
        self.exposure_spin.setDecimals(3)
        self.exposure_spin.setValue(100.0)
        self.exposure_spin.setAlignment(Qt.AlignRight)
        # FIXED 2026-09-17 (user: "software slows down when some changes are
        # made"): on_exposure_changed() does a camera SDK write + readback
        # and a synchronous waveform-config disk save on every valueChanged.
        # Without this, each of those runs once per keystroke while typing a
        # number (Qt parses every partial digit) -- see _spin() in this file
        # for the same fix already applied to the ROI/pixel fields.
        self.exposure_spin.setKeyboardTracking(False)
        # Not on the LouisXIV panel (it is an ini setting there); placed in the
        # box's empty band. Read by the acquisition start.
        self.sync_readout_chk = QCheckBox("Sync readout (frame = exposure)", self)
        self.sync_readout_chk.setGeometry(*_rect(30, 232, 206, 18))
        self.sync_readout_chk.setChecked(True)
        self._label("Sensor Mode", _rect(30, 394, 90, 21))
        self.sensor_mode_combo = QComboBox(self)
        self.sensor_mode_combo.setGeometry(*_rect(125, 394, 110, 21))   # +4 px: Qt's arrow is wider than LabVIEW's
        self.sensor_mode_combo.addItems(list(Camera.SENSOR_MODES))
        self.sensor_mode_combo.currentTextChanged.connect(self._on_sensor_mode)

        # Actual (read-backs)
        self._label("Actual", _rect(297, 151, 60, 18), bold=True)
        self.actual_exposure = self._readback(_rect(253, 203, 76, 18))
        self.actual_frame_time = self._readback(_rect(253, 227, 76, 18))
        self.actual_rate = self._readback(_rect(334, 227, 54, 18))
        self._readback(_rect(253, 252, 138, 18), "1 exposure(s)")  # static, as on the LouisXIV panel
        for f in (self.actual_exposure, self.actual_frame_time, self.actual_rate):
            f.setAlignment(Qt.AlignRight)

        # ROI box
        self._label("ROI", _rect(28, 540, 40, 16))
        self._box(_rect(28, 558, 115, 107), white=True)
        right = Qt.AlignRight | Qt.AlignVCenter
        self._label("Left", _rect(32, 564, 44, 20), align=right)
        self._label("Right", _rect(32, 586, 44, 20), align=right)
        self._label("Top", _rect(32, 617, 44, 20), align=right)
        self._label("Bottom", _rect(32, 639, 44, 20), align=right)
        self.roi_left = self._spin(_rect(81, 564, 56, 20), 1, 2048, 1)
        self.roi_right = self._spin(_rect(81, 586, 56, 20), 1, 2048, 2048)
        self.roi_top = self._spin(_rect(81, 617, 56, 20), 1, 2048, 1)
        self.roi_bottom = self._spin(_rect(81, 639, 56, 20), 1, 2048, 2048)
        for s in (self.roi_left, self.roi_right, self.roi_top, self.roi_bottom):
            s.valueChanged.connect(self._on_roi_edited)

        # # of pixels box
        self._label("# of pixels", _rect(157, 541, 70, 16))
        self._box(_rect(157, 557, 79, 84), white=True)
        self._label("X", _rect(161, 563, 14, 18))
        self._label("Y", _rect(161, 617, 14, 18))
        self.pix_x = self._spin(_rect(177, 563, 53, 18), 4, 2048, 2048)
        self.pix_y = self._spin(_rect(177, 617, 53, 18), 4, 2048, 2048)
        self.pix_x.valueChanged.connect(self._on_pixels_edited)
        self.pix_y.valueChanged.connect(self._on_pixels_edited)

        # FOV box
        self._label("FOV", _rect(158, 658, 40, 16))
        self._box(_rect(158, 674, 84, 84), white=True)
        self._label("X", _rect(162, 680, 14, 18))
        self._label("Y", _rect(162, 734, 14, 18))
        self.fov_x = self._readback(_rect(178, 680, 58, 18))
        self.fov_y = self._readback(_rect(178, 734, 58, 18))

        # ROI center box + buttons
        self._label("ROI center", _rect(159, 804, 70, 16))
        self._box(_rect(159, 820, 82, 53), white=True)
        self._label("X", _rect(163, 826, 14, 18))
        self._label("Y", _rect(163, 849, 14, 18))
        self.roi_center_x = self._spin(_rect(179, 826, 56, 18), 0, 2048, 0)
        self.roi_center_y = self._spin(_rect(179, 849, 56, 18), 0, 2048, 0)
        self.center_roi_btn = self._button("Center  ROI", _rect(37, 670, 92, 31), self.on_center_roi)
        self.use_all_btn = self._button("Use all pixels", _rect(37, 706, 92, 31), self.on_use_all_pixels)
        self.roi_1024_btn = self._button("1024x1024", _rect(37, 747, 92, 31), lambda: self.on_preset(1024))
        self.roi_512_btn = self._button("512x512", _rect(37, 783, 92, 31), lambda: self.on_preset(512))
        self.center_at_btn = self._button("Center  ROI at", _rect(37, 823, 92, 31), self.on_center_roi_at)

        # ---- Detection Optics (UNMScope addition -- no LouisXIV panel or VI
        # for this; nothing in docs/vi_notes.md) --------------------------------
        # FOV = pixel count x sample-space pixel size, and sample-space pixel
        # size = camera sensor pixel (um) / objective magnification
        # (Detection.pixel_size_um, unmscope/config/calibration.py). Both
        # numbers used to be editable only by hand-editing the ini's
        # [Detection optics] section (Magnification), or not at all (Camera
        # Pixel (um) was hardcoded, never read from the ini) -- user,
        # 2026-09-23: "the FOV should be calculated from the magnification of
        # the objective in use and the pixel size camera... should we add a
        # section to the camera tab". Not pixel-matched to anything real --
        # plain coordinates, not the _rect() measured-panel mapper. Placed
        # in the blank column right of the ROI/# of pixels/FOV boxes (the
        # real panel has nothing there below "Actual"'s bottom, page y~160,
        # down to the ROI center box at y~448) -- moved up from below the
        # panel's own content (user, same day: "so I don't have to scroll
        # down to see it"), and this spot needs no extra scroll height at all.
        opt_x, opt_top = 244, 180
        self._label("Detection Optics", (opt_x, opt_top, 145, 16), bold=True)
        self._box((opt_x, opt_top + 16, 145, 132), white=True)
        self._label("Magnification (x)", (opt_x + 6, opt_top + 22, 130, 16))
        self.magnification_spin = QDoubleSpinBox(self)
        self.magnification_spin.setGeometry(opt_x + 6, opt_top + 38, 90, 20)
        self.magnification_spin.setRange(1.0, 200.0)
        self.magnification_spin.setDecimals(2)
        self.magnification_spin.setSuffix("x")
        self.magnification_spin.setValue(self._initial_magnification)
        self.magnification_spin.setKeyboardTracking(False)
        self.magnification_spin.setToolTip(
            "Objective magnification (e.g. 30 for a 30x). Drives FOV and the "
            "sample-space pixel size (Camera Pixel Size / Magnification) -- "
            "click Set Optics to apply and save it.")
        self._label("Cam. Pixel Size (um)", (opt_x + 6, opt_top + 62, 130, 16))
        self.camera_pixel_spin = QDoubleSpinBox(self)
        self.camera_pixel_spin.setGeometry(opt_x + 6, opt_top + 78, 90, 20)
        self.camera_pixel_spin.setRange(0.1, 50.0)
        self.camera_pixel_spin.setDecimals(3)
        self.camera_pixel_spin.setValue(self._initial_camera_pixel_um)
        self.camera_pixel_spin.setKeyboardTracking(False)
        self.camera_pixel_spin.setToolTip(
            "Physical camera sensor pixel pitch (Orca Flash 4.0 = 6.5 um).")
        self.set_optics_btn = self._button("Set Optics", (opt_x + 6, opt_top + 104, 90, 26),
                                           self._on_set_detection)
        self.set_optics_btn.setToolTip(
            "Apply these values to FOV/pixel-size calculations now, and save "
            "them so they come back on the next launch.")

    # -- state -------------------------------------------------------------------
    def persistent_widgets(self) -> dict:
        """Camera settings worth carrying into the next session (see
        unmscope.config.ui_state). The ROI corners are stored rather than
        the derived width/height boxes, which recompute from them.

        Exposure is deliberately NOT included (user, 2026-09-23: "The
        exposure time should be 100ms by default") -- same reasoning as
        Hold/window geometry in main_window.py's _persistent_widgets:
        coming up at a fixed, known-safe value beats silently carrying over
        whatever was last typed, which read as a bug, not a convenience."""
        return {"cam_sync_readout": self.sync_readout_chk,
                "cam_sensor_mode": self.sensor_mode_combo,
                "cam_roi_left": self.roi_left, "cam_roi_right": self.roi_right,
                "cam_roi_top": self.roi_top, "cam_roi_bottom": self.roi_bottom}

    @property
    def roi(self) -> Roi:
        return self._roi

    def sensor_mode(self) -> str:
        return self.sensor_mode_combo.currentText()

    def _show_roi(self, roi: Roi) -> None:
        self._updating = True
        try:
            hmax, vmax = self._sensor
            for s in (self.roi_left, self.roi_right):
                s.setRange(1, hmax)
            for s in (self.roi_top, self.roi_bottom):
                s.setRange(1, vmax)
            self.pix_x.setRange(self._units[2], hmax)
            self.pix_y.setRange(self._units[3], vmax)
            self.roi_center_x.setRange(0, hmax)
            self.roi_center_y.setRange(0, vmax)
            self.roi_left.setValue(roi.left)
            self.roi_right.setValue(roi.right)
            self.roi_top.setValue(roi.top)
            self.roi_bottom.setValue(roi.bottom)
            self.pix_x.setValue(roi.width)
            self.pix_y.setValue(roi.height)
            pix = self._pixel_size_um(1)
            self.fov_x.setText(f"{fov_um(roi.width, pix):.1f} um")
            self.fov_y.setText(f"{fov_um(roi.height, pix):.1f} um")
        finally:
            self._updating = False

    def refresh_fov(self) -> None:
        """Re-read pixel_size_um(1) (e.g. after Set Optics changes the
        calibration this tab was built with) and redraw FOV X/Y."""
        self._show_roi(self._roi)

    def _on_set_detection(self) -> None:
        self._set_detection(self.magnification_spin.value(), self.camera_pixel_spin.value())

    def _set_roi(self, roi: Roi) -> Roi:
        hpu, vpu, hu, vu = self._units
        coerced = coerce_roi(roi, *self._sensor, hposunit=hpu, vposunit=vpu, hunit=hu, vunit=vu)
        self._roi = coerced
        self._show_roi(coerced)
        self._push_to_camera()
        self.roi_changed.emit(coerced)
        return coerced

    # -- edits -------------------------------------------------------------------
    def _on_roi_edited(self, _value=None) -> None:
        if self._updating:
            return
        self._set_roi(Roi(self.roi_left.value(), self.roi_top.value(),
                          self.roi_right.value(), self.roi_bottom.value()))

    def _on_pixels_edited(self, _value=None) -> None:
        if self._updating:
            return
        new = (self.pix_x.value(), self.pix_y.value())
        old = (self._roi.width, self._roi.height)
        if new != old:
            self._set_roi(adjust_roi_for_pixels(self._roi, new, old))

    def on_use_all_pixels(self) -> None:
        self._set_roi(full_roi(*self._sensor))

    def on_preset(self, size: int) -> None:
        self._set_roi(centered_roi(size, size, *self._sensor))

    def on_center_roi(self) -> None:
        self._set_roi(center_roi(self._roi, *self._sensor))

    def on_center_roi_at(self) -> None:
        self._set_roi(center_roi_at(self._roi, self.roi_center_x.value(), self.roi_center_y.value()))

    def _on_sensor_mode(self, mode: str) -> None:
        if self._updating:
            return
        self._push_to_camera()

    # -- camera --------------------------------------------------------------------
    def _idle_camera(self) -> Camera | None:
        cam = self._get_camera()
        if cam is None or not cam.is_connected:
            return None
        return cam

    def _push_to_camera(self) -> None:
        """Apply now if the camera is idle; otherwise leave it for Acquire."""
        cam = self._idle_camera()
        if cam is None:
            return
        try:
            if cam.is_sequence_running():
                if not self.pending_apply:
                    self._log("Camera ROI / sensor mode will be applied at the next Acquire "
                              "(the sequence stays armed until then).")
                self.pending_apply = True
                return
            self.apply_to_camera(cam)
        except Exception as e:
            self._log(f"Camera settings failed: {type(e).__name__}: {e}")

    def apply_to_camera(self, cam: Camera) -> bool:
        """'DCAM - Set parameters' at scan start: push sensor mode and ROI if
        they differ from what the camera has. Stops an armed sequence first
        (DCAM refuses subarray changes while capturing). Returns True if
        anything was changed on the camera."""
        want_mode = self.sensor_mode()
        changed = False
        if cam.get_sensor_mode() != want_mode or cam.get_roi() != self._roi:
            if cam.is_sequence_running():
                cam.stop_sequence()
                self._log("Camera sequence stopped to change ROI / sensor mode.")
            if cam.get_sensor_mode() != want_mode:
                cam.set_sensor_mode(want_mode)
                changed = True
            if cam.get_roi() != self._roi:
                got = cam.set_roi(self._roi)
                changed = True
                if got != self._roi:
                    self._log(f"Camera coerced the ROI to L{got.left} R{got.right} T{got.top} B{got.bottom}.")
                    self._roi = got
                    self._show_roi(got)
        self.pending_apply = False
        return changed

    def refresh_from_camera(self) -> None:
        """After connect: sensor size / units, and the camera's current ROI,
        binning, sensor mode and exposure read-backs."""
        cam = self._idle_camera()
        if cam is None:
            return
        self._sensor = cam.sensor_size()
        self._units = cam.roi_units()
        self._roi = cam.get_roi()
        self._updating = True
        try:
            self.sensor_mode_combo.setCurrentText(cam.get_sensor_mode())
        finally:
            self._updating = False
        info = cam.info
        self.camera_name_field.setText(info.name if info is not None else "")
        self._show_roi(self._roi)
        self.pending_apply = False
        self.refresh_actuals()

    def refresh_actuals(self) -> None:
        cam = self._idle_camera()
        if cam is None:
            return
        try:
            exposure = cam.get_exposure_ms()
            period = cam.trigger_period_ms(exposure)
        except Exception as e:
            self._log(f"Camera read-back failed: {type(e).__name__}: {e}")
            return
        self.actual_exposure.setText(f"{exposure:.4f} ms")
        self.actual_frame_time.setText(f"{period:.4f} ms")
        self.actual_rate.setText(f"{1000.0 / period:.4g} Hz" if period > 0 else "")

    def on_disconnected(self) -> None:
        self.camera_name_field.setText("")
        for f in (self.actual_exposure, self.actual_frame_time, self.actual_rate):
            f.setText("")
        self.pending_apply = False
