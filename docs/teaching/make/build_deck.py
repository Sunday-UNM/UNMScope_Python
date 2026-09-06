# -*- coding: utf-8 -*-
import base64, re
from pathlib import Path

HERE = Path(__file__).resolve().parent          # docs/teaching/make/
OUT = HERE.parent                               # docs/teaching/
SHOTS = HERE / "shots_crop"
DECK = OUT / "how-unmscope-is-put-together.html"
SRC = HERE / "unmscope-deck.src.html"


def uri(name):
    b = (SHOTS / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(b).decode("ascii")


S = {p.stem: uri(p.name) for p in SHOTS.glob("*.png")}

html = SRC.read_text(encoding="utf-8")

# ---------------------------------------------------------------- CSS
CSS = """
/* .slide sets display:flex, which otherwise beats the browser's own
   [hidden] rule and shows every slide at once */
[data-slide][hidden] { display: none !important; }

/* real screenshots, mounted on the dark slide */
figure.shot { margin: 0; background: var(--panel-2); border: 1px solid var(--rule-lit); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; }
figure.shot img { display: block; max-width: 100%; height: auto; max-height: 62vh; border-radius: 4px; }
figure.shot figcaption { width: 100%; font-family: "IBM Plex Mono", monospace; font-size: .72rem; letter-spacing: .04em; color: var(--ink-dim); padding: 9px 3px 1px; }
figure.shot figcaption b { color: var(--ink-soft); font-weight: 500; }
.shots-2 { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(14px, 2vw, 26px); align-items: stretch; margin-top: 4px; }
/* equal-height mounts, so a caption or a panel under one screenshot sits on the
   same line as the one beside it even when the two images differ in shape */
.shots-2 > div { display: flex; flex-direction: column; }
.shots-2 > div > figure.shot { flex: 1; }
.shots-2.pair figure.shot img { max-height: 40vh; }
@media (max-width: 880px) { .shots-2 { grid-template-columns: 1fr; } }
.cards.stack { grid-template-columns: 1fr; }
.two.shot-right { grid-template-columns: 1.02fr 1fr; }
@media (max-width: 880px) { .two.shot-right { grid-template-columns: 1fr; } }
.note { font-size: .95rem; color: var(--ink-dim); font-weight: 300; border-left: 2px solid var(--l637); padding-left: 14px; margin-top: 16px; }
.note b { color: var(--ink-soft); font-weight: 500; }
</style>"""
html = html.replace("</style>", CSS, 1)


def slide(body):
    return '<section class="slide" data-slide hidden>\n' + body.strip() + '\n</section>\n'


s2 = slide("""
  <p class="eyebrow">Before anything else</p>
  <h2>A program is just <em>text files in folders</em></h2>
  <p class="sub">There is nothing hidden or compiled here. Every part of this software is a plain
  text file you can open in Notepad, and the folder names <strong>are</strong> the organisation.
  Learning your way around it is exactly like learning your way around someone's filing cabinet.
  Two words worth knowing, because they turn up everywhere:</p>
  <div class="shots-2 pair">
    <div>
      <figure class="shot">
        <img src="__SHOT09__" alt="Notepad showing the top of camera.py: a long explanatory note in triple quotes, then the import lines">
        <figcaption><b>camera.py</b> &mdash; 31,748 characters, plain text, nothing else</figcaption>
      </figure>
      <div class="panel" style="margin-top:14px">
        <h3>A &ldquo;module&rdquo; is one file</h3>
        <p>One <code>.py</code> file holding related code. <code>camera.py</code> is the camera
        module. When someone says &ldquo;it's in the camera module&rdquo;, they mean open that file
        &mdash; and this is all opening it means.</p>
      </div>
    </div>
    <div>
      <figure class="shot">
        <img src="__SHOT04__" alt="Explorer showing the hardware folder: camera.py alongside fake_fpga.py, fpga_scope.py, fpga_trigger.py, louisxiv_waveform.py, roi.py, stage.py and waveform.py">
        <figcaption><b>hardware\\</b> &mdash; the folder that same file lives in</figcaption>
      </figure>
      <div class="panel" style="margin-top:14px">
        <h3>A &ldquo;package&rdquo; is one folder of them</h3>
        <p>A folder of modules that works as a unit. <code>hardware\\</code> is one:
        <code>camera.py</code> and seven siblings. The whole program is itself a package, called
        <code>unmscope</code>.</p>
      </div>
    </div>
  </div>
""")

s3 = slide("""
  <p class="eyebrow">The widest view</p>
  <h2>Three folders side by side</h2>
  <p class="sub">All under <code>H:\\UNM_Lightsheet\\</code>. They are deliberately siblings &mdash; none is
  inside another &mdash; so it is always obvious which world a file belongs to.</p>
  <div class="two">
    <div class="cards stack">
      <div class="card">
        <span class="lab">the original</span>
        <h3>UNMScope_Source\\</h3>
        <p>The LabVIEW program that runs the microscope today, plus this rig's calibration file.
        We read from it constantly and <b>never write to it</b>.</p>
      </div>
      <div class="card">
        <span class="lab">a readable copy</span>
        <h3>VI_Diagrams\\</h3>
        <p>A picture of every page of that program, in the same folder shape. This is what we
        actually read, because LabVIEW files are drawings, not text.</p>
      </div>
      <div class="card lit">
        <span class="lab">the new program</span>
        <h3>UNMScope_Python\\</h3>
        <p>Everything we write. The rest of this talk is inside this one folder.</p>
      </div>
    </div>
    <figure class="shot">
      <img src="__SHOT01__" alt="Windows Explorer at H:\\UNM_Lightsheet showing the folders UNMScope_Python, UNMScope_Source and VI_Diagrams">
      <figcaption><b>H:\\UNM_Lightsheet\\</b> &mdash; the three folders, as Explorer shows them</figcaption>
    </figure>
  </div>
""")

s4 = slide("""
  <p class="eyebrow">One level down</p>
  <h2>Opening the project folder</h2>
  <p class="sub">Five folders and five files. Every one has a single job, and nothing lives outside
  them. The two entries starting with a dot are housekeeping &mdash; Python's own scratch space and our
  written-down notes for the assistant.</p>
  <div class="two shot-right">
    <pre class="tree"><b>UNMScope_Python\\</b>
&#9474;
&#9500;&#9472;&#9472; <u>src\\</u>          <i>the actual program</i>
&#9500;&#9472;&#9472; <u>tests\\</u>        <i>automatic checks</i>
&#9500;&#9472;&#9472; <u>spikes\\</u>       <i>hardware experiments</i>
&#9500;&#9472;&#9472; <u>tools\\</u>        <i>utilities for us</i>
&#9500;&#9472;&#9472; <u>docs\\</u>         <i>what we measured, and why</i>
&#9474;
&#9500;&#9472;&#9472; <b>README.md</b>       <i>what this project is</i>
&#9500;&#9472;&#9472; <b>ROADMAP.md</b>      <i>done &middot; next &middot; undecided</i>
&#9500;&#9472;&#9472; <b>CLAUDE.md</b>       <i>the house rules</i>
&#9500;&#9472;&#9472; <b>COMMANDS.md</b>     <i>copy-paste cheat sheet</i>
&#9492;&#9472;&#9472; <b>pyproject.toml</b>  <i>what must be installed</i></pre>
    <figure class="shot">
      <img src="__SHOT02__" alt="Explorer listing of the UNMScope_Python folder: docs, spikes, src, tests, tools and the five top-level files">
      <figcaption><b>UNMScope_Python\\</b> &mdash; the same thing, unedited</figcaption>
    </figure>
  </div>
""")

s6b = slide("""
  <p class="eyebrow">src\\unmscope\\</p>
  <h2>The drawers, actually open</h2>
  <div class="two shot-right">
    <div class="body">
      <p>The same five drawers on screen. Two extra entries appear here that are worth naming, because
      they turn up in every Python folder you will ever open.</p>
      <p><code>__init__.py</code> is the marker that makes a folder into a package. Ours is three lines
      long. Its whole job is to say <strong>&ldquo;this folder is one unit&rdquo;</strong>.</p>
      <p><code>__pycache__</code> is Python's own scratch space, written automatically whenever the
      program runs. It is never edited by hand and never kept with the project.</p>
      <p class="note"><b>And an honest one.</b> <code>messaging\\</code> and <code>state_machine\\</code>
      are empty. They were sketched on the first day as homes for a design we ended up not needing, and
      nothing in the program refers to them. Real projects collect this sort of thing. The useful habit
      is saying so out loud rather than assuming every folder matters.</p>
    </div>
    <figure class="shot">
      <img src="__SHOT03__" alt="Explorer listing of src backslash unmscope: analysis, config, fileio, gui, hardware, messaging, state_machine, __pycache__ and __init__.py">
      <figcaption><b>src\\unmscope\\</b> &mdash; seven folders, five of them in use</figcaption>
    </figure>
  </div>
""")

s7 = slide("""
  <p class="eyebrow">The files themselves</p>
  <h2>Names you will meet</h2>
  <p class="sub">Names are meant to be boring and literal. If you want the camera's settings panel, it
  is <code>gui\\camera_tab.py</code>. Nothing clever. Two drawers, opened:</p>
  <div class="shots-2">
    <figure class="shot">
      <img src="__SHOT04__" alt="Explorer listing of the hardware folder: camera.py, fake_fpga.py, fpga_scope.py, fpga_trigger.py, louisxiv_waveform.py, roi.py, stage.py and waveform.py">
      <figcaption><b>hardware\\</b> &mdash; one file per piece of the instrument</figcaption>
    </figure>
    <figure class="shot">
      <img src="__SHOT05__" alt="Explorer listing of the gui folder: main_window.py, camera_tab.py, scope_view.py, utilities_tab.py, display.py and the dialog files">
      <figcaption><b>gui\\</b> &mdash; one file per panel on screen</figcaption>
    </figure>
  </div>
  <p class="sub" style="margin-top:22px">The other three drawers are smaller. <code>config\\</code>
  holds <code>calibration</code>, <code>um_per_volt</code>, <code>hw_config</code>,
  <code>spim_ini</code> and <code>paths</code>. <code>fileio\\</code> holds <code>tiff_stack</code>
  and <code>stage_locations</code>. <code>analysis\\</code> holds <code>projections</code>.</p>
""")

s11 = slide("""
  <p class="eyebrow">tests\\</p>
  <h2>A mirror of the program</h2>
  <div class="two shot-right">
    <div class="body">
      <p>For nearly every file in the program there is a matching file of checks, named the same way
      with <code>test_</code> in front. Change <code>roi.py</code> and you look at
      <code>test_roi.py</code>. You can read the mirror straight off this listing.</p>
      <p>A check is simply: <strong>set up a known situation, do the thing, state what the answer must
      be.</strong> If the answer ever changes, the check fails and names itself.</p>
      <p>All 246 of them run without a microscope, in about 35 seconds, and they run before every save
      of the work. That is the safety net that makes it reasonable to keep changing this software.</p>
      <p class="note"><b>conftest.py</b> is the odd name out. It is the shared setup every check uses.
      Here it points the program at a temporary folder, so running the tests can never disturb your
      real settings.</p>
    </div>
    <figure class="shot">
      <img src="__SHOT06__" alt="Explorer listing of the tests folder showing files named test_calibration.py, test_camera_tab.py, test_roi.py and so on">
      <figcaption><b>tests\\</b> &mdash; 28 files, 246 checks</figcaption>
    </figure>
  </div>
""")

s12 = slide("""
  <p class="eyebrow">spikes\\ and tools\\</p>
  <h2>The folders that are <em>not</em> the program</h2>
  <div class="two shot-right">
    <div>
      <div class="panel">
        <h3>spikes\\ &mdash; 38 numbered scripts</h3>
        <p>Each answers one question about the real hardware: <em>does this pin actually put out five
        volts? is the trigger really 100 milliseconds?</em> They are numbered in the order they were
        run and kept forever, like a lab notebook. You can read the whole bring-up straight down the
        listing.</p>
        <p style="margin-top:12px">They are not part of the program and never run on their own. Their
        value is as evidence. When someone asks how we know the laser is on that pin, the answer is a
        numbered script and the measurement it produced.</p>
      </div>
      <div class="panel">
        <h3>tools\\ &mdash; 3 utilities for us</h3>
        <p>A live panel for poking at the card by hand, a monitor for the signal stream, and a script
        that screenshots the old and new programs and prints how many pixels apart their layouts are.
        Never seen by whoever is using the microscope.</p>
      </div>
    </div>
    <figure class="shot">
      <img src="__SHOT08__" alt="Explorer listing of the spikes folder showing numbered scripts from 01_fpga_connect.py through 26_gui_simulate_on_fpga_headless.py">
      <figcaption><b>spikes\\</b> &mdash; the bring-up, in the order it happened</figcaption>
    </figure>
  </div>
""")

s13 = slide("""
  <p class="eyebrow">docs\\</p>
  <h2>Why a fact is a fact</h2>
  <div class="two shot-right">
    <div class="body">
      <p>Fourteen notes recording what was <strong>measured</strong>, as opposed to what was assumed.
      Each one names the part of the old program it came from and the date it was checked on the
      instrument.</p>
      <p>This exists because six months later nobody remembers why a number is 6600 rather than zero,
      and guessing again is how mistakes get reintroduced.</p>
      <div class="panel" style="margin-top:18px">
        <h3>Reading the listing</h3>
        <p><code>fpga_io_map.md</code> &mdash; which physical connector each signal comes out of,
        verified with a meter.</p>
        <p style="margin-top:10px"><code>known_issues.md</code> &mdash; traps we have already fallen
        into, so nobody falls in twice.</p>
        <p style="margin-top:10px"><code>camera_tab.md</code> &mdash; for one panel: what each control
        does, which LabVIEW page it came from, and a table of how far off our layout is in pixels.</p>
      </div>
    </div>
    <figure class="shot">
      <img src="__SHOT07__" alt="Explorer listing of the docs folder showing camera_tab.md, fpga_io_map.md, known_issues.md and the other measurement notes">
      <figcaption><b>docs\\</b> &mdash; one note per thing we had to establish</figcaption>
    </figure>
  </div>
""")

# ---------------------------------------------------------------- splice
parts = re.split(r"\n<!-- (\d+) -->\n", html)
head = parts[0]
seg = {}
for i in range(1, len(parts), 2):
    seg[int(parts[i])] = parts[i + 1]

tail_marker = '\n</div>\n\n<div class="nav">'
assert tail_marker in seg[16], "tail marker not found"
seg[16], tail = seg[16].split(tail_marker, 1)
tail = tail_marker + tail

seg[2] = s2
seg[3], seg[4], seg[7], seg[11], seg[12], seg[13] = s3, s4, s7, s11, s12, s13
seg["6b"] = s6b

order = [1, 2, 3, 4, 5, 6, "6b", 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

out = [head]
for n, key in enumerate(order, start=1):
    out.append("\n<!-- %d -->\n" % n)
    out.append(seg[key].rstrip() + "\n")
out.append(tail)
new = "".join(out)

new = new.replace('<span id="tot">16</span>', '<span id="tot">17</span>')

for k, v in [("01", "01_three_folders"), ("02", "02_project_root"), ("03", "03_package"),
             ("04", "04_hardware"), ("05", "05_gui"), ("06", "06_tests"),
             ("07", "07_docs"), ("08", "08_spikes"),
             ("09", "09_camera_module")]:
    new = new.replace("__SHOT%s__" % k, S[v])

assert "__SHOT" not in new
DECK.write_text(new, encoding="utf-8")
print("slides:", new.count("data-slide"))
print("images:", new.count("data:image/png;base64,"))
print("size:", len(new.encode("utf-8")) // 1024, "KB")
