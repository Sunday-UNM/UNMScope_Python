"""Hardware abstraction layer.

One ABC per device category (camera, stage, filter wheel, AO mirror, FPGA
scan engine), each with a Simulated* backend, per ROADMAP.md Phase 1.

Status: `camera.py` has a working Camera ABC + SimulatedCamera +
OrcaFlash4Camera (real, via pymmcore-plus). `stage.py` has the XYZStage /
RotationStage ABCs, SimulatedMP285 / SimulatedRotationStage, and the Sutter
MP-285 serial protocol over an injected transport (not bench-verified: the
stage is not connected to this rig). Filter wheel, AO mirror and the FPGA
scan engine as ABCs: not yet started.
"""
