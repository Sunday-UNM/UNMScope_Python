"""LouisXIV's 'Waveform' cluster as a dataclass: panel defaults, JSON round
trip, and the Z-motion gate the builder uses."""
import pytest

from unmscope.config.waveform_config import AxisSettings, WaveformConfig, camera_cycle_s, engine_times


def test_defaults_are_the_low_level_waveform_config_panel():
    """Read off the LIVE LouisXIV panel, 2026-09-07 (Adv Setup > Low-Level
    Waveform Config, this rig's own running configuration) -- not the COM
    render the first pass used, which disagreed with the rig on eight
    fields. Fractional Flyback and Fract. Smoothing feed the ramp shape
    directly, so the computed X waveform does not match LouisXIV's until
    these do."""
    c = WaveformConfig()
    assert c.waveform == "Linear" and c.pixel_per_ms == 0.5 and c.updates_per_pix == 1
    assert c.fractional_flyback == 0.15 and c.fract_smoothing == 0.0
    assert c.x_galvo_delay_us == 0.0 and c.duty_pct == 100.0 and c.cam_exp_s == 2.0
    assert c.z_motion == "Z galvo & piezo" and c.x_wave == "Sawtooth" and c.z_wave == "Step"
    assert c.aotf_cycle == "None" and c.wait_for_z_settle == "No settle"
    assert c.x_triangle_pulses == 1.0 and c.dither_triangle_pulses == 2.5 and c.dither_fract_flyback == 0.0
    assert c.n_doe_beams == 0
    assert c.aotf_pulse_duty_pct == 5.0 and c.z_piezo_selector == 1
    assert c.z == AxisSettings(0, 0.0, 0.2, 4) and c.zpiezo == AxisSettings(0, 0.0, 0.0, 20)
    assert c.x == AxisSettings(0, 0.0, 0.2, 2) and c.xwvfrm.pixels == 1


def test_json_round_trip(tmp_path):
    c = WaveformConfig(fractional_flyback=0.25, dither_triangle_pulses=3.5, z=AxisSettings(1, 2.0, 0.5, 8))
    p = c.save(tmp_path / "wc.json")
    back = WaveformConfig.load(p)
    assert back == c
    assert WaveformConfig.load(tmp_path / "missing.json") == WaveformConfig()
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert WaveformConfig.load(tmp_path / "bad.json") == WaveformConfig()


def test_from_dict_ignores_unknown_and_missing_fields():
    c = WaveformConfig.from_dict({"fractional_flyback": 0.3, "bogus": 1, "zpiezo": {"pixels": 5}})
    assert c.fractional_flyback == 0.3 and c.zpiezo.pixels == 5 and c.zpiezo.size == 0.0
    assert c.updates_per_pix == 1


def test_z_axes_moving():
    assert WaveformConfig().z_axes_moving() == (True, True)
    assert WaveformConfig(z_motion="Z piezo").z_axes_moving() == (False, True)
    assert WaveformConfig(z_motion="Z galvo").z_axes_moving() == (True, False)


# -- camera_cycle_s / engine_times: LouisXIV's Cam exp / Cycle time formula ------------
# docs/louisxiv_cycle_time_semantics.md (Q1/Q2/Q3, reconciled 2026-09-06: the user
# confirmed Custom Cycle Time is ticked on this rig, resolving the doc's open tension).

def test_camera_cycle_s_syncreadout_exposure_dominates_at_full_frame():
    # 100 ms exposure, 2048 rows: the readout term ((1024+18)*1H =~ 10.15 ms)
    # is far below the exposure, so the camera cycle IS the exposure.
    assert camera_cycle_s(0.1, 2048, sync_readout=True) == pytest.approx(0.1)
    # the sub-array case that halved the frame count in spikes/36-37: same
    # result at 512 rows -- the readout term shrinks with height, it never grows.
    assert camera_cycle_s(0.1, 512, sync_readout=True) == pytest.approx(0.1)


def test_camera_cycle_s_syncreadout_readout_dominates_at_short_exposure():
    # 1 ms exposure, 2048 rows: now the readout term wins.
    assert camera_cycle_s(0.001, 2048, sync_readout=True) == pytest.approx((1024 + 18) * 9.74436e-6)


def test_camera_cycle_s_syncreadout_clamps_at_ten_seconds():
    assert camera_cycle_s(20.0, 2048, sync_readout=True) == pytest.approx(10.0)


def test_camera_cycle_s_edge_adds_readout_to_exposure():
    # EDGE has no SyncReadout collapse: it is always exposure + readout term.
    assert camera_cycle_s(0.1, 2048, sync_readout=False) == pytest.approx(0.1 + (1024 + 10) * 9.74436e-6)


def test_engine_times_custom_off_always_the_floor():
    # Custom OFF: the typed value (0.127) is ignored entirely.
    cam_exp_s, cycle_s = engine_times(0.1, 0.1000421, 0.127, False)
    assert cam_exp_s == pytest.approx(0.1) and cycle_s == pytest.approx(0.1000416)  # floor - 500 ns


def test_engine_times_custom_on_keeps_a_typed_value_above_the_floor():
    cam_exp_s, cycle_s = engine_times(0.1, 0.1000421, 0.127, True)
    assert cam_exp_s == pytest.approx(0.1) and cycle_s == pytest.approx(0.127)


def test_engine_times_custom_on_floors_but_never_lowers_a_typed_value():
    # 0.05 typed is below the ~0.1 floor: raised to it, not left at 0.05.
    _, cycle_s = engine_times(0.1, 0.1000421, 0.05, True)
    assert cycle_s == pytest.approx(0.1000416)
