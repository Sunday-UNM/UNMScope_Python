"""Small helpers shared by the panel modules.

Deliberately narrow. The 2026-09-05 audit proposed folding the six panel
modules' constructor helpers (`_label`, `_button`, `_checkbox`, `_combo`,
`_spin`, ...) into one shared set. Reading them side by side, that is the
wrong call: the six `_label`s differ in ways that are not incidental --
`calibration_tab` paints a transparent background, `camera_debug_panel`
sets a colour but no background, `sample_stage_dialog` sets both,
`hw_config_dialog` word-wraps and applies its own rect transform plus a
vertical nudge, `waveform_config_panel` right-aligns. A single helper
serving all six would take five keyword flags and be harder to read than
the six six-line functions it replaced.

So only what is *identical* lives here: the bold-font idiom, the
measured-rect-to-page-rect mapper, and the show/raise/activate sequence for
a tool window. Each was written out in full in three to eight places.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QWidget

Rect = tuple[int, int, int, int]


def set_bold(widget: QWidget) -> QWidget:
    """Make a widget's font bold, returning it so it can be used inline.

    Qt has no setBold on the widget, so this is the three-line
    get-font / set-bold / set-font dance, which appeared eight times.

    Named ``set_bold`` rather than ``bold`` because every panel's ``_label``
    takes a ``bold=False`` keyword, which would shadow it inside exactly the
    functions that want to call it.
    """
    f = widget.font()
    f.setBold(True)
    widget.setFont(f)
    return widget


def rect_mapper(origin_x: int, origin_y: int) -> Callable[[int, int, int, int], Rect]:
    """Build the "measured rect -> page rect" translator a panel needs.

    Layouts here are measured off a full-window capture of the LouisXIV
    panel, then placed inside a page or tab whose own origin is not (0, 0).
    Every panel module had its own one-line ``_rect`` / ``_pg`` / ``_r``
    differing only in which origin constants it subtracted.

        _rect = rect_mapper(OX, OY)
    """
    def to_page(x: int, y: int, w: int, h: int) -> Rect:
        return (x - origin_x, y - origin_y, w, h)
    return to_page


def bring_to_front(window: QWidget) -> None:
    """Show a tool window and put it in front, even if it was already open.

    ``show()`` alone leaves an already-open window buried behind the main
    window, which reads as the button doing nothing.
    """
    window.show()
    window.raise_()
    window.activateWindow()
