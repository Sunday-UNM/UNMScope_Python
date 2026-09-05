"""LouisXIV's 'Waveform' cluster as a dataclass: panel defaults, JSON round
trip, and the Z-motion gate the builder uses."""
from unmscope.config.waveform_config import AxisSettings, WaveformConfig


def test_defaults_are_the_low_level_waveform_config_panel():
    c = WaveformConfig()
    assert c.waveform == "Linear" and c.pixel_per_ms == 25.6 and c.updates_per_pix == 1
    assert c.fractional_flyback == 0.1 and c.fract_smoothing == 1.0
    assert c.x_galvo_delay_us == 0.02 and c.duty_pct == 0.05 and c.cam_exp_s == 2.0
    assert c.z_motion == "Z galvo & piezo" and c.x_wave == "Sawtooth" and c.z_wave == "Step"
    assert c.aotf_cycle == "per Stack" and c.wait_for_z_settle == "No settle"
    assert c.x_triangle_pulses == 5.5 and c.dither_triangle_pulses == 5.5 and c.dither_fract_flyback == 0.1
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
