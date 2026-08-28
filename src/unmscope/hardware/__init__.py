"""Hardware abstraction layer.

One ABC per device category (camera, stage, filter wheel, AO mirror, FPGA
scan engine), each with a Simulated* backend, per ROADMAP.md Phase 1. Real
backends (Andor, Hamamatsu DCAM, Thorlabs, PI, Imagine Optics, nifpga) are
added one subsystem at a time in later phases -- none exist yet.
"""
