# BOX-3 mascot animations

Pixel-art mascots for the BOX-3 voice status screen (HIMMEL-7815). The screen shows the
mascot of the agent that answers: Claude, Codex, or Grokbot when a "grok" pipeline
answers. Each mascot has five states: idle, listen, think, speak and error.

## Files

- `<agent>_<state>.gif`: one looping animation per mascot and state, 96x96 frames.
- `mascots.yaml`: the ESPHome package. It loads every GIF as an RGB565 `animation` with
  the id `<agent>_<state>`, sets `fps_<agent>_<state>` for each, and sets `mascot_x` and
  `mascot_y` (112, 66), which centre the 96 px clip in the 128 px box the screen keeps
  for the mascot at 96, 50. The firmware takes each clip's frame count from the file.
- `palette.gpl`: the shared GIMP palette, 31 colours.
- `preview_<agent>.gif`: every state of one mascot at 3x, about two seconds each, with
  its name underneath. For review only; the firmware does not load these.
- `concept.png`: the first idle frame of each mascot at 4x.
- `check_sprites.py`: the stdlib check, `python3 -I check_sprites.py`.

## Format and budget

- Frames: 96x96, drawn on a 48x48 grid and scaled 2x (nearest), opaque, on the
  screen's background colour (16, 20, 32).
- Every colour sits on the RGB565 grid (red and blue multiples of 8, green multiples
  of 4), so the screen shows exactly these colours.
- Clips, frames and fps:

  | state  | frames | fps | loop length |
  |--------|--------|-----|-------------|
  | idle   | 8      | 5   | 1.6 s       |
  | listen | 6      | 8   | 0.75 s      |
  | think  | 6      | 6   | 1 s         |
  | speak  | 4      | 8   | 0.5 s       |
  | error  | 3      | 6   | 0.5 s       |

- ESPHome plays every frame of a clip for the same time and stores each frame as raw
  RGB565, so a hold has to be a distinct pose, never a repeated frame: Pillow merges two
  identical neighbours into one. No two neighbouring frames are identical, including
  the last and the first.
- Flash: 81 frames x 96 x 96 x 2 bytes = 1,492,992 bytes, under the 1,500,000 byte budget
  the check enforces.

## The mascots

All three are original characters drawn for this repo. No vendor logo, mark or
trademark is copied or traced, and no third-party asset is reused, so there is no
licence to cite.

- **Claude**: a round terracotta clay creature with a cream face, rosy cheeks, nub
  arms and feet, and one ink-curl tuft, like a pen stroke or a question curl. A warm,
  gentle, bookish companion.
  - Idle: breathes, rising and stretching; the tuft sways; it blinks.
  - Listen: the tuft stands up, eyes go wide, arms lift, sound rings ripple out.
  - Think: eyes look up, the tuft tip glows, an amber spark orbits the head and passes
    behind it.
  - Speak: the mouth goes closed, half, open, half, with a bounce and a gesturing arm.
  - Error: squeezed eyes, a drooping tuft, a sweat drop, a red "!" and a shake.
- **Codex**: a beige CRT terminal on two little legs, with a cable tail ending in an
  amber plug. Its face is green phosphor on a scanlined screen. Diligent and precise.
  - Idle: bobs on its legs, the cursor blinks, the tail wags, it blinks.
  - Listen: wide eyes, sound rings, an input level meter on the bezel.
  - Think: its eyes glance down at code lines scrolling up the screen.
  - Speak: waveform bars are the mouth.
  - Error: the screen turns red with X eyes, a glitch shake and a red "!".
- **Grokbot**: a slate dome robot hovering on a violet jet, with one wide visor, a bent
  off-centre antenna and a crooked grin, one eye at half-lid. Wry and cheeky.
  - Idle: hovers and breathes, the jet flickers, the antenna light pulses, it blinks.
  - Listen: the ear bolts glow blue, eyes go round, sound rings ripple out.
  - Think: a bright scanner sweeps the visor back and forth.
  - Speak: a lit grille mouth.
  - Error: red static fills the visor behind white X eyes, a frown, a red "!" and a
    shake.

## The check

`check_sprites.py` fails unless `mascots.yaml` loads every `<agent>_<state>.gif` with a
positive fps and sets `mascot_x` and `mascot_y`. Every GIF must also:

- decode to opaque square frames of one shared size (96 to 128 px);
- have the frames its state needs (idle 8 to 12);
- never repeat a frame back to back;
- use only `palette.gpl` colours, all of them exact in RGB565;
- keep the total RGB565 size under the budget.

It decodes the GIFs itself (LZW), with no third-party packages.
