"""
Stage F -- multi-trigger burst validation (Z-stack building block).

We've only ever fired exactly ONE trigger before. Z-stack mode needs a
burst of N triggers that stops on its own. Two competing hypotheses for
how N is actually controlled:

  (A) The `Trigger #s`/`Trigger stack #s`/`Trigger blast #s` clusters'
      '# on' field is a hardware-enforced pulse count -- set '# on' = N,
      hold Trigger Enable? briefly (like the single-trigger case), and
      the FPGA fires exactly N pulses on its own then stops.
  (B) Holding Trigger Enable? asserted for longer just keeps firing once
      per Cycle(Ticks) period for as long as it's held (this is literally
      how we accidentally got a SECOND pulse in earlier testing when we
      held it too long) -- i.e. WE control the count via hold duration,
      and the '# on' fields don't limit anything by themselves.

This script tests hypothesis (A) first: '# on' = N_TRIGGERS, held for a
SHORT time (~10ms, same as the validated single-trigger case). Watch the
oscilloscope and count pulses. If you see exactly N_TRIGGERS pulses then
it stops -- (A) is confirmed. If you see only 1 (or something else), we
fall back to testing (B) next.

No camera involved -- FPGA/DIO4 only, oscilloscope verification.
"""
import time

import nifpga

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

AO_MODE_START_RUN_WVFRM = 0
AO_MODE_SET_AO = 2
AO_MODE_CLEAR_AO_DMA = 3  # never used before -- testing whether AO DMA Error is sticky/latched

N_TRIGGERS = 5  # back to the real target now that we reset() at the start of each fresh run
TRIGGER_UP_TICKS = 4_000_000  # ~100ms @ 40MHz, same as validated single-pulse test
CAM_TRIGGER_DELAY_TICKS = 0
AO_TICKS_BETWEEN_POINTS = 4000
AO_POINTS_PER_TRIGGER = 1

# ROOT CAUSE FOUND: a 700ms hold underflowed the Wvfrm2 FIFO when it only
# held 96 words -- at AO ticks between points=4000 (100us), 700ms needs
# ~7000 samples. AO DMA Error showed Buffer Underflow=True, AO wvfrm ready
# dropped to False, and that's why triggering went completely silent (the
# trigger generator depends on the AO waveform engine actively running).
# Size generously for whatever hold time we're testing.
ZERO_WVFRM_WORDS = [0] * 20000
WAIT_READY_TIMEOUT_S = 3.0
# FIXED: 5 pulses physically cannot fit in a 10ms hold if each pulse cycle
# takes ~100ms (Cycle(Ticks)/Trigger up (ticks)). Hold long enough for the
# FULL expected N-pulse burst plus margin, so we can actually tell whether
# '# on'=N is a hard limit (stops at N even though we hold longer) or not
# (keeps firing for as long as held, ignoring '# on').
CYCLE_S = TRIGGER_UP_TICKS / 40e6
ENABLE_HOLD_S = 0.7  # fixed 700ms -- EXACTLY matching the failed N=5 tests, for a clean isolation test
REPEAT_COUNT = 3
REPEAT_PAUSE_S = 5.0


def main():
    print(f"=== Multi-trigger burst test: N={N_TRIGGERS}, testing hypothesis (A) ===\n")
    print(f"Opening FPGA session ({RESOURCE})...")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        print("Connected. SW Version:", session.registers["SW Version"].read())

        # Always reset+run at the start of a fresh experiment -- earlier
        # testing showed a stuck/latched AO DMA error state can silently
        # contaminate every subsequent attempt in the same session until
        # explicitly reset. Cheap insurance.
        print("Resetting FPGA for a clean start (session.reset() + run())...")
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        print("AO DMA Error after reset:", session.registers["AO DMA Error"].read())

        session.registers["AO Mode"].write(AO_MODE_SET_AO)
        zero_static = {
            "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
            "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
        }
        session.registers["Static AO to set"].write(zero_static)
        session.registers["Set F.P. (T)"].write(True)
        print("Confirmed safe (all-zero) static state before starting.")

        session.registers["Cam Trigger delay (ticks)"].write(CAM_TRIGGER_DELAY_TICKS)
        session.registers["# of triggers"].write(N_TRIGGERS)  # <-- N here too
        session.registers["Continuous Mode"].write(False)
        session.registers["Cycle(Ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["AO Trigger delay (ticks)"].write(0)
        session.registers["Free run"].write(False)
        session.registers["AI # of channels"].write(0)
        session.registers["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
        session.registers["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
        session.registers["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
        session.registers['Shutter Ticks "on" (Ticks)'].write(0)

        # Hypothesis (A) with '# on'=N produced ZERO pulses (a regression
        # from the known-working single-trigger case) -- so try hypothesis
        # (C) instead: leave these clusters at the validated single-pulse
        # values (1,0) regardless of N, and let '# of triggers' alone
        # control the count.
        on_off_one = {"# on": 1, "# off": 0}
        session.registers["Trigger #s"].write(on_off_one)
        session.registers["Trigger stack #s"].write(on_off_one)
        session.registers["Trigger blast #s"].write(on_off_one)
        session.registers["Set F.P. (T)"].write(True)
        print(f"Full register bundle written: '# of triggers'=N={N_TRIGGERS}, "
              f"but Trigger #s/stack/blast clusters left at the validated "
              f"single-pulse (1,0) -- testing hypothesis (C).")

        print(f"\nWill fire the N={N_TRIGGERS} burst {REPEAT_COUNT} times, "
              f"{REPEAT_PAUSE_S:.0f}s apart. Countdown happens BEFORE arming "
              f"this time (fill FIFO -> ready -> fire happen back-to-back, "
              f"minimizing how long the AO engine drains before we fire) -- "
              f"count exactly how many pulses appear each time.")
        for rep in range(1, REPEAT_COUNT + 1):
            for c in (3, 2, 1):
                print(f"  arming+firing in {c}...")
                time.sleep(1.0)

            # Fill + arm + fire tightly sequenced -- minimize drain time
            # before the trigger, which is what actually caused the
            # underflow (the AO engine drains continuously once armed,
            # regardless of Trigger Enable? state).
            fifo = session.fifos["Wvfrm2"]
            fifo.stop()

            # Explicitly clear any latched AO DMA error before arming --
            # AO DMA Error/Buffer Underflow may be sticky, not a live status.
            session.registers["AO Mode"].write(AO_MODE_CLEAR_AO_DMA)
            session.registers["Set F.P. (T)"].write(True)
            time.sleep(0.05)
            print(f"    AO DMA Error after Clear: {session.registers['AO DMA Error'].read()}")

            fifo.start()
            fifo.write(ZERO_WVFRM_WORDS, timeout_ms=2000)

            session.registers["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
            session.registers["Set F.P. (T)"].write(True)
            t0 = time.time()
            ready = False
            while time.time() - t0 < WAIT_READY_TIMEOUT_S:
                ready = session.registers["AO wvfrm ready"].read()
                if ready:
                    break
                time.sleep(0.005)
            if not ready:
                print(f"  AO wvfrm ready never went True (waited {time.time()-t0:.2f}s) -- skipping fire.")
                continue

            print(f">>> FIRING BURST {rep}/{REPEAT_COUNT} NOW -- watch/count on the scope <<<")
            t_fire = time.time()
            session.registers["Trigger Enable?"].write(True)
            session.registers["Set F.P. (T)"].write(True)
            time.sleep(ENABLE_HOLD_S)
            session.registers["Trigger Enable?"].write(False)
            session.registers["Set F.P. (T)"].write(True)
            print(f"  Trigger Enable? cleared at t+{time.time()-t_fire:.3f}s.")
            # Give any in-flight pulses time to finish before checking status.
            time.sleep((TRIGGER_UP_TICKS / 40e6) * (N_TRIGGERS + 1))
            try:
                print(f"    AO DMA Error: {session.registers['AO DMA Error'].read()}")
                print(f"    AO wvfrm ready: {session.registers['AO wvfrm ready'].read()}")
            except Exception as e:
                print(f"    (status register read failed: {e})")

            # Back to safe static between reps.
            session.registers["AO Mode"].write(AO_MODE_SET_AO)
            session.registers["Static AO to set"].write(zero_static)
            session.registers["Set F.P. (T)"].write(True)
            fifo.stop()

            if rep < REPEAT_COUNT:
                print(f"  Pausing {REPEAT_PAUSE_S:.0f}s before next repeat...")
                time.sleep(REPEAT_PAUSE_S)

        print("\nReturned to safe (all-zero) static state.")

    print("Session closed.")
    print(f"\nHow many pulses appeared per burst this time (testing hypothesis C: "
          f"'# of triggers'=N={N_TRIGGERS} alone, Trigger #s clusters left at (1,0))?")
    print(f"  - exactly {N_TRIGGERS} each time -> '# of triggers' IS the real count control (hypothesis C)")
    print(f"  - exactly 1 each time -> '# of triggers' doesn't matter either; something else gates count")
    print(f"  - zero again -> still broken somehow, needs more isolation")


if __name__ == "__main__":
    main()
