"""The one surgical editor for LouisXIV-style ini files.

Three modules write into the UNMScope copy of ``SPIMProject.ini``:
:mod:`unmscope.config.spim_ini`, :mod:`unmscope.config.hw_config` and
:mod:`unmscope.config.um_per_volt`. Each had grown its own writer and they
did not agree: one worked on bytes, one on latin-1 text, and one decoded the
file as UTF-8 with ``errors="replace"``, which would turn any byte LouisXIV
wrote that is not valid UTF-8 into U+FFFD on the way back out. That last one
never actually bit us -- the rig's SPIMProject.ini happens to be pure ASCII
today -- but it was one non-ASCII character away from quietly damaging the
file. This is the survivor of the three: byte-faithful, and the only one now.

Verified against the real file: a write that changes no value leaves it
byte-identical, and changing one key alters exactly that one line.

Why not :mod:`configparser`: writing through it reflows the whole file --
comments, blank lines, key spacing and the ``Key = Value`` alignment all
change. LouisXIV's file is edited by LouisXIV too, so a save must touch
exactly the keys it means to and leave every other byte alone. Reading is
still done with configparser; only writing needs this.

Two quirks of LouisXIV's format the editor has to honour:

* ``#`` does **not** start a comment. ``# Pts (Default) = 5`` is a key whose
  name begins with a hash.
* The file has no final newline in places, and mixes its line endings; both
  are preserved rather than normalised.
"""
from __future__ import annotations

from pathlib import Path

#: LouisXIV's ini is not UTF-8. latin-1 maps every byte 0-255 to exactly one
#: code point and back, so a read/edit/write round-trip is byte-exact
#: whatever the file actually contains.
ENCODING = "latin-1"


class IniText:
    """A byte-faithful ini editor: only the value of a key that is set is
    touched (its ``Key = `` prefix, trailing whitespace and line ending
    stay); missing keys are appended to their section, missing sections to
    the file. Everything else round-trips untouched."""

    def __init__(self, text: str):
        # An empty file means we are creating one, and LouisXIV's is CRLF
        # throughout, so default to CRLF rather than to the platform's "\n".
        self.nl = "\r\n" if "\r\n" in text or not text else "\n"
        self.lines = text.splitlines(keepends=True)

    def text(self) -> str:
        return "".join(self.lines)

    def _section_span(self, section: str) -> tuple[int, int] | None:
        start = None
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if start is not None:
                    return start, i
                if s[1:-1].strip() == section:
                    start = i
        return None if start is None else (start, len(self.lines))

    @staticmethod
    def _split(line: str) -> tuple[str, str, str, str] | None:
        """'Key = Value   \\r\\n' -> (prefix 'Key = ', value, trailing ws, eol)."""
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        if "=" not in body or body.lstrip().startswith("["):
            return None     # '#' is NOT a comment here: "# Pts (Default) = 5" is a key
        k, _, rest = body.partition("=")
        stripped = rest.lstrip()
        prefix = k + "=" + rest[:len(rest) - len(stripped)]
        value = stripped.rstrip()
        trailing = stripped[len(value):]
        return prefix, value, trailing, eol

    def get(self, section: str, key: str) -> str | None:
        span = self._section_span(section)
        if span is None:
            return None
        for i in range(span[0] + 1, span[1]):
            parts = self._split(self.lines[i])
            if parts and parts[0].split("=")[0].strip() == key:
                return parts[1]
        return None

    def set(self, section: str, key: str, value: str) -> None:
        span = self._section_span(section)
        if span is None:
            self._ensure_final_newline()
            if self.lines and self.lines[-1].strip():
                # Readability only. MEASURED: LouisXIV's own SPIMProject.ini
                # has NO blank line between sections, and no final newline
                # either. But it never writes the sections we append, and a
                # blank line here is inert to every reader of the file.
                self.lines.append(self.nl)
            self.lines.append(f"[{section}]{self.nl}")
            self.lines.append(f"{key} = {value}{self.nl}")
            return
        start, end = span
        for i in range(start + 1, end):
            parts = self._split(self.lines[i])
            if parts and parts[0].split("=")[0].strip() == key:
                prefix, _old, trailing, eol = parts
                self.lines[i] = f"{prefix}{value}{trailing}{eol}"
                return
        # key missing: insert after the section's last non-blank line
        insert = end
        while insert - 1 > start and not self.lines[insert - 1].strip():
            insert -= 1
        if insert == len(self.lines):
            self._ensure_final_newline()
        self.lines.insert(insert, f"{key} = {value}{self.nl}")

    def _ensure_final_newline(self) -> None:
        if self.lines and not self.lines[-1].endswith(("\n", "\r")):
            self.lines[-1] += self.nl


def read_text(path: str | Path) -> str:
    """The file's bytes as latin-1, with line endings left exactly as found.
    Returns "" if it does not exist yet."""
    p = Path(path)
    if not p.exists():
        return ""
    with open(p, encoding=ENCODING, newline="") as fh:
        return fh.read()


def write_text(path: str | Path, text: str) -> None:
    """Write ``text`` back byte-for-byte, creating the parent if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding=ENCODING, newline="") as fh:
        fh.write(text)


def write_keys(path: str | Path, section: str, values: dict[str, str]) -> None:
    """Replace (or append) ``values`` in ``[section]``, touching nothing else.

    The file is left alone entirely when no value actually changes, so a save
    that changes nothing does not update the modification time.
    """
    raw = read_text(path)
    ini = IniText(raw)
    for key, value in values.items():
        ini.set(section, key, value)
    out = ini.text()
    if out != raw:
        write_text(path, out)
