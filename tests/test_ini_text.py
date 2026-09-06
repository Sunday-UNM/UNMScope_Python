"""The one ini writer, shared by spim_ini / hw_config / um_per_volt.

The guarantee that matters: LouisXIV edits SPIMProject.ini too, so a save
must change exactly the keys it means to and leave every other byte alone.
"""
import pytest

from unmscope.config.ini_text import IniText, read_text, write_keys


def test_a_write_that_changes_nothing_leaves_the_file_byte_identical(tmp_path):
    p = tmp_path / "x.ini"
    original = b"[A]\r\nk1 = v1\r\nk2   =    spaced   \r\n\r\n[B]\r\nk3=v3"
    p.write_bytes(original)
    write_keys(p, "A", {"k1": "v1"})
    assert p.read_bytes() == original


def test_changing_one_key_changes_exactly_one_line(tmp_path):
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\nk1 = v1\r\nk2   =    spaced   \r\n\r\n[B]\r\nk3=v3")
    write_keys(p, "A", {"k1": "new"})
    before = b"[A]\r\nk1 = v1\r\nk2   =    spaced   \r\n\r\n[B]\r\nk3=v3".split(b"\r\n")
    after = p.read_bytes().split(b"\r\n")
    assert len(before) == len(after)
    assert [i for i, (b, a) in enumerate(zip(before, after)) if b != a] == [1]


def test_key_spacing_and_trailing_whitespace_survive(tmp_path):
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\nk2   =    spaced   \r\n")
    write_keys(p, "A", {"k2": "9"})
    assert p.read_bytes() == b"[A]\r\nk2   =    9   \r\n"


def test_a_file_with_no_final_newline_keeps_none(tmp_path):
    """LouisXIV's real SPIMProject.ini ends mid-line, with no trailing CRLF."""
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\nk1 = v1")
    write_keys(p, "A", {"k1": "2"})
    assert p.read_bytes() == b"[A]\r\nk1 = 2"


def test_hash_does_not_start_a_comment(tmp_path):
    """'# Pts (Default) = 5' is a key whose name begins with a hash."""
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\n# Pts (Default) = 5\r\n")
    assert IniText(read_text(p)).get("A", "# Pts (Default)") == "5"
    write_keys(p, "A", {"# Pts (Default)": "7"})
    assert p.read_bytes() == b"[A]\r\n# Pts (Default) = 7\r\n"


def test_a_new_file_is_written_crlf_like_louisxivs(tmp_path):
    p = tmp_path / "new.ini"
    write_keys(p, "A", {"k": "1"})
    assert p.read_bytes() == b"[A]\r\nk = 1\r\n"


@pytest.mark.parametrize("missing", ["key", "section"])
def test_missing_keys_and_sections_are_appended(tmp_path, missing):
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\nk1 = v1\r\n\r\n[B]\r\nk2 = v2\r\n")
    if missing == "key":
        write_keys(p, "A", {"k9": "9"})
        # into its own section, after its last key, not at the end of the file
        assert p.read_bytes() == b"[A]\r\nk1 = v1\r\nk9 = 9\r\n\r\n[B]\r\nk2 = v2\r\n"
    else:
        write_keys(p, "C", {"k9": "9"})
        assert p.read_bytes() == b"[A]\r\nk1 = v1\r\n\r\n[B]\r\nk2 = v2\r\n\r\n[C]\r\nk9 = 9\r\n"


def test_the_real_louisxiv_ini_round_trips_byte_for_byte(tmp_path):
    """The check that actually matters, against the rig's own file."""
    from unmscope.config.paths import LOUISXIV_SUPPORT_DIR
    src = LOUISXIV_SUPPORT_DIR / "SPIMProject.ini"
    if not src.exists():
        pytest.skip("LouisXIV's SPIMProject.ini is not on this machine")
    p = tmp_path / "copy.ini"
    original = src.read_bytes()
    p.write_bytes(original)

    value = IniText(read_text(p)).get("Detection optics", "Magnification")
    write_keys(p, "Detection optics", {"Magnification": value})
    assert p.read_bytes() == original, "a no-op save must not touch the file"

    write_keys(p, "Detection optics", {"Magnification": "31"})
    before, after = original.split(b"\r\n"), p.read_bytes().split(b"\r\n")
    assert len(before) == len(after)
    changed = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert len(changed) == 1 and after[changed[0]].endswith(b"31")
