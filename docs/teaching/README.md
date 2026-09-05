# Teaching materials

Two self-contained HTML pages written to explain this project to students.
Both open in any browser with no server and no internet connection beyond the
web fonts. Every screenshot is embedded in the file itself.

| File | What it is |
|---|---|
| `how-unmscope-is-put-together.html` | A 17-slide deck about the **structure**: folders, files, naming, and how one action moves down the stack. Arrow keys move between slides. |
| `unmscope-field-guide.html` | The same ground in prose, for reading rather than presenting. |

Published copies:

- Deck — <https://claude.ai/code/artifact/8d2a0f00-41db-41ad-8953-5ca67ff55f89>
- Field guide — <https://claude.ai/code/artifact/c222c042-d65b-45ed-b894-fb9e72391ef9>

## Rebuilding the deck

The deck is generated, not hand-edited. Edit `make/unmscope-deck.src.html`
for anything that is only text, or `make/build_deck.py` for the slides that
carry screenshots, then:

```
python docs/teaching/make/build_deck.py
```

That rewrites `how-unmscope-is-put-together.html` in place. Republishing it to
the artifact link above keeps the same URL.

## Rebuilding the screenshots

`make/shots_crop/` holds the eight cropped Explorer listings the deck embeds.
They were captured on this machine at 1440x1080. To redo them:

1. `powershell make/capture_folders.ps1` — opens Explorer at each folder,
   sizes every window to 1000x700, grabs it, closes it. Writes
   `make/shots_raw/`.
2. `powershell make/capture_tall.ps1` — the same for `tests`, `docs` and
   `spikes`, at 1000x1010, because those listings are longer than a short
   window shows.
3. `python make/crop_shots.py` — crops the breadcrumb strip and the file list
   out of each grab and composites them into `make/shots_crop/`.
4. `python make/build_deck.py` — re-embeds them.

Two details in the crop that are easy to get wrong. The bottom of the list is
found by scanning only the Name, Date and Type columns; scanning the full
width catches the view-mode buttons in the window corner and every crop comes
out the same height. And the left edge sits at x=224 to clear the navigation
pane.

## A note on accuracy

The deck describes five folders under `src/unmscope/`, and the screenshot of
that folder shows seven. The two extra ones, `messaging/` and
`state_machine/`, hold nothing but a placeholder docstring from the first day
of the project and are referenced nowhere. Rather than crop that out of the
picture, the deck names it. If those folders are ever deleted, update the
slide and re-capture `03_package.png`.
