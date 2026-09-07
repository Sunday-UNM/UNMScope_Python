"""Remember the panel settings the user last chose, between sessions.

The complaint this exists for: "why do the software keep going to default
options that you have stored. Why not keep the last checked options given
[by] the user as the starting point for the next session."

Every session started from hard-coded defaults -- DEFAULT_ACTIVE for the
scope's channel ticks, 488 nm for the laser, 20 ms/div for the timebase --
so any arrangement the user built up was thrown away on exit and had to be
rebuilt by hand next time. Only the Low-Level Waveform Config survived,
through :mod:`unmscope.config.waveform_config`; this is the same idea for
everything else, in one file next to it.

The store is a flat ``{name: value}`` JSON dict. Names are chosen by whoever
registers the widget (see ``MainWindow._persistent_widgets``), so adding a
control to the remembered set is one line, and a name that disappears from
the code is simply ignored on the next load rather than breaking startup.

What is deliberately NOT remembered:

- **Window geometry.** The window is pixel-matched to LouisXIV's front panel
  and that match is checked (CLAUDE.md); a stale saved size would silently
  break it.
- **Hold**, and anything else that would have the app come up in a stopped
  or frozen state, which reads as broken rather than as restored.
- **Connection state.** Nothing here connects to hardware; it only fills in
  the controls, and the user still presses Connect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QSlider, QWidget

from unmscope.config.paths import user_dir


def default_path() -> Path:
    return user_dir() / "ui_state.json"


def widget_value(w: QWidget) -> Any:
    """The one value that defines this widget's setting, or None if the type
    is not one we know how to store."""
    if isinstance(w, QAbstractButton):
        return bool(w.isChecked()) if w.isCheckable() else None
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, QAbstractSpinBox):
        return w.value()          # QSpinBox / QDoubleSpinBox both
    if isinstance(w, QSlider):
        return w.value()
    return None


def set_widget_value(w: QWidget, value: Any) -> bool:
    """Apply a stored value. Returns False (changing nothing) when the value
    does not fit the widget any more -- a combo item that has been renamed, a
    spin box whose range has narrowed, a saved type that no longer matches.
    A settings file from an older build must never stop the GUI starting.
    """
    try:
        if isinstance(w, QAbstractButton):
            if not w.isCheckable() or not isinstance(value, bool):
                return False
            w.setChecked(value)
            return True
        if isinstance(w, QComboBox):
            i = w.findText(str(value))
            if i < 0:
                return False
            w.setCurrentIndex(i)
            return True
        if isinstance(w, QAbstractSpinBox):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            # Clamp rather than reject: a spin box whose range has tightened
            # should still land as near the remembered value as it can.
            w.setValue(type(w.value())(min(max(value, w.minimum()), w.maximum())))
            return True
        if isinstance(w, QSlider):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            w.setValue(int(min(max(value, w.minimum()), w.maximum())))
            return True
    except (TypeError, ValueError, OverflowError):
        return False
    return False


def collect(widgets: dict[str, QWidget]) -> dict[str, Any]:
    """{name: widget} -> {name: value}, skipping types with no value."""
    out: dict[str, Any] = {}
    for name, w in widgets.items():
        v = widget_value(w)
        if v is not None:
            out[name] = v
    return out


def apply(widgets: dict[str, QWidget], state: dict[str, Any]) -> list[str]:
    """Fill the widgets in from a stored state. Returns the names restored.

    Names in the file that no longer exist, and values that no longer fit,
    are ignored -- see set_widget_value.
    """
    done = []
    for name, value in (state or {}).items():
        w = widgets.get(name)
        if w is not None and set_widget_value(w, value):
            done.append(name)
    return done


def load(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path is not None else default_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}                      # a corrupt file is a fresh start, not a crash
    return data if isinstance(data, dict) else {}


def save(state: dict[str, Any], path: str | Path | None = None) -> Path:
    p = Path(path) if path is not None else default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return p
