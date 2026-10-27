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
- `palette.gpl`: the shared GIMP palette, 24 colours.
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

These are fan-art derivatives of the vendors' marks, made for a personal device. Each
mark is redrawn as a pixel character with a face and its own motion; no logo file is
copied or traced, and no third-party asset is reused. The Claude, Anthropic, OpenAI,
Codex, Grok and xAI names and marks are trademarks of their owners, and this repo is
not affiliated with or endorsed by them.

- **Claude**: the coral starburst, twelve rays of uneven length on Anthropic's warm
  coral and cream, with an ink face and rosy cheeks on its core.
  - Idle: the rays breathe in a slow wave; it blinks.
  - Listen: the rays stretch out with a ripple running round them, wide eyes, an "o"
    mouth.
  - Think: the burst turns and its uneven outline morphs as it goes, eyes up, amber
    thought dots.
  - Speak: the rays pulse with the mouth: closed, half, open, half.
  - Error: the rays shrink and shake, it flashes red, X eyes and a red "!".
- **Codex**: the six-loop knot, white and monochrome, with a black face in its centre
  and eyes and mouth in a blue-violet accent.
  - Idle: the knot sways gently; it blinks.
  - Listen: the knot swells, sound arcs ripple out.
  - Think: the knot spins a sixth of a turn per loop, eyes up, amber thought dots.
  - Speak: the knot pulses with the mouth.
  - Error: the knot greys out and flashes red, X eyes, a shake and a red "!".
- **Grokbot**: the slashed circle, a white ring on a black face with the diagonal slash
  crossing the ring's gap.
  - Idle: a glint runs along the slash; it blinks.
  - Listen: the ring swells, sound arcs ripple out.
  - Think: a notch orbits the ring, eyes up, amber thought dots.
  - Speak: the slash bobs with the mouth.
  - Error: the slash drops, the ring flashes red, X eyes and a red "!".

## The check

`check_sprites.py` fails unless `mascots.yaml` loads every `<agent>_<state>.gif` with a
positive fps and sets `mascot_x` and `mascot_y`. Every GIF must also:

- decode to opaque square frames of one shared size (96 to 128 px);
- have the frames its state needs (idle 8 to 12);
- never repeat a frame back to back;
- use only `palette.gpl` colours, all of them exact in RGB565;
- keep the total RGB565 size under the budget.

It decodes the GIFs itself (LZW), with no third-party packages.
