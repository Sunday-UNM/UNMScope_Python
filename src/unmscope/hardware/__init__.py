"""Hardware abstraction layer.

One ABC per device category (camera, stage, filter wheel, AO mirror, FPGA
scan engine), each with a Simulated* backend, per ROADMAP.md Phase 1.

Status: `camera.py` has a working Camera ABC + SimulatedCamera +
OrcaFlash4Camera (real, via pymmcore-plus). Other categories (stage,
filter wheel, AO mirror, FPGA scan engine) not yet started.
"""
