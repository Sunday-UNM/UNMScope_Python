"""LouisXIV's stage-location files: byte-compatible round trips on the real
backup files, the loader's column rules, and the two table objects."""
import math
from pathlib import Path

import pytest

from unmscope.fileio import stage_locations as sl
from unmscope.fileio.stage_locations import (
    LocationSequence, SavedLocations, cell_to_float, format_location_row, load_location_file,
    save_location_file,
)

BACKUP = Path(r"H:\UNM_Lightsheet\UNMScope_Source\SPIM\SPIM Support files\backup")
LOCATIONS_BYTES = (b"41\t1\t1.00\t2.00\t3.00\tNaN\t25.0000\r\n"
                   b"41\t3\t50.00\t60.00\t70.00\tNaN\t272.0000\r\n")


def test_loader_column_rules(tmp_path):
    p = tmp_path / "loc.txt"
    p.write_bytes(b"41\ta\t1\t2\t3\tNaN\t4\r\n"        # 7 columns: OK
                  b"42\tb\t1\t2\t3\r\n"                # 5 columns: add a 6th ('--')
                  b"41\tc\t1\t2\t3\t0.5\r\n"           # 6 columns: add a 7th
                  b"bad\trow\r\n"                      # thrown out
                  b"\r\n")
    rows, loaded = load_location_file(p)
    assert loaded and len(rows) == 3
    assert rows[0] == ["41", "a", "1", "2", "3", "NaN", "4"]
    assert rows[1] == ["42", "b", "1", "2", "3", "--", ""]
    assert rows[2] == ["41", "c", "1", "2", "3", "0.5", "--"]
    assert load_location_file(tmp_path / "missing.txt") == ([], False)
    assert math.isnan(cell_to_float("--")) and math.isnan(cell_to_float("NaN")) and cell_to_float(" 2.5 ") == 2.5


def test_saved_locations_round_trip_matches_louisxiv_bytes(tmp_path):
    p = tmp_path / "loc.txt"
    p.write_bytes(LOCATIONS_BYTES)
    s = SavedLocations(p)
    assert s.load() and len(s) == 2 and s.locked == [False, False]
    assert s.rows[0] == ["1", "1.00", "2.00", "3.00", "NaN", "25.0000"]
    assert s.xyz(0) == (1.0, 2.0, 3.0)
    rel, theta = s.rel_offset_and_theta(1)
    assert math.isnan(rel) and theta == 272.0
    s.save()
    assert p.read_bytes() == LOCATIONS_BYTES
    # the GUI's consumer cases
    assert s.toggle_lock(0) is True
    s.insert(format_location_row("new", 1.234, 2.0, -3.5, None, 12.34567))
    assert s.rows[2] == ["new", "1.23", "2.00", "-3.50", "NaN", "12.3457"]
    s.update(1, format_location_row("3", 50, 60, 70, 0.25, None))
    assert s.rows[1] == ["3", "50.00", "60.00", "70.00", "0.250000", "NaN"]
    s.remove_all_unlocked()
    assert s.rows == [["1", "1.00", "2.00", "3.00", "NaN", "25.0000"]] and s.locked == [True]
    s.save()
    assert p.read_bytes() == b"42\t1\t1.00\t2.00\t3.00\tNaN\t25.0000\r\n"
    s.remove(0)
    s.save()
    assert p.read_bytes() == b""


@pytest.mark.skipif(not (BACKUP / "SPIMProject 3D Stage Locations.txt").exists(), reason="LouisXIV backup files absent")
def test_real_backup_files_round_trip(tmp_path):
    loc = tmp_path / "loc.txt"
    loc.write_bytes((BACKUP / "SPIMProject 3D Stage Locations.txt").read_bytes())
    s = SavedLocations(loc)
    s.load()
    s.save()
    assert loc.read_bytes() == (BACKUP / "SPIMProject 3D Stage Locations.txt").read_bytes()
    seq = tmp_path / "seq.txt"
    seq.write_bytes((BACKUP / "SPIMProject 3D Stage Sequence Locations.txt").read_bytes())
    q = LocationSequence(seq)
    rows = q.load()
    assert len(rows) == 64 and rows[1][2] == "83.20"                # the 2x2x16 grid (wc -l: 64)
    q.write(rows)
    assert seq.read_bytes() == (BACKUP / "SPIMProject 3D Stage Sequence Locations.txt").read_bytes()


def test_location_sequence_write_renumbers_and_notifies(tmp_path):
    p = tmp_path / "seq.txt"
    q = LocationSequence(p)
    assert q.load() == [] and not q.loaded
    seen = []
    q.add_listener(lambda s: seen.append(len(s)))
    q.append_saved(["a", "1.00", "2.00", "3.00", "NaN", "25.0000"])
    q.append_saved(["b", "4.00", "5.00", "6.00", "0.5", "NaN"])
    assert q.rows[0][0] == "1" and q.rows[1][0] == "2" and seen == [1, 2]
    assert p.read_bytes() == (b"1\ta\t1.00\t2.00\t3.00\tNaN\t25.0000\r\n"
                              b"2\tb\t4.00\t5.00\t6.00\t0.5\tNaN\r\n")
    assert q.positions_um() == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
    rel = q.rel_offsets_um()
    assert math.isnan(rel[0]) and rel[1] == 0.5
    th = q.thetas_deg()
    assert th[0] == 25.0 and math.isnan(th[1])
    assert q.max_move_time_s(1000, 300) == pytest.approx(3 / 1000 + 0.3)
    assert q.max_move_time_s(1000, 300, enabled=False) == 0.0
    q.remove(0)
    assert q.rows == [["1", "b", "4.00", "5.00", "6.00", "0.5", "NaN"]]
    q.remove_all()
    assert q.rows == [] and p.read_bytes() == b"" and seen == [1, 2, 1, 0]
    assert LocationSequence(p).load() == []


def test_user_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "USER_DIR", tmp_path / "home")
    src = tmp_path / "src.txt"
    src.write_bytes(LOCATIONS_BYTES)
    dest = sl.ensure_user_copy(src, sl.user_locations_path())
    assert dest == tmp_path / "home" / sl.LOCATIONS_FILENAME and dest.read_bytes() == LOCATIONS_BYTES
    src.write_bytes(b"")
    assert sl.ensure_user_copy(src, dest).read_bytes() == LOCATIONS_BYTES     # copied once
    assert not sl.ensure_user_copy(tmp_path / "nope.txt", sl.user_sequence_path()).exists()
    save_location_file(tmp_path / "deep" / "x.txt", [["1", "n", "0.00", "0.00", "0.00", "NaN", "NaN"]])
    assert (tmp_path / "deep" / "x.txt").read_bytes() == b"1\tn\t0.00\t0.00\t0.00\tNaN\tNaN\r\n"
