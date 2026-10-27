# BOX-3 mascot sprites

Pixel-art mascots for the BOX-3 voice status screen (HIMMEL-7815), one per agent, in
four voice states. `preview.png` shows all twelve strips: one row per agent (claude,
codex, grokbot) and one column per state (idle, listen, think, speak).

## Files

- `<agent>_<state>.png`: one strip per agent and state, 4 frames side by side, 384x96.
  Agents `claude`, `codex`, `grokbot`; states `idle`, `listen`, `think`, `speak`.
- `palette.gpl`: the shared GIMP palette, 31 colours.
- `preview.png`: all twelve strips in one grid, for review only.
- `check_sprites.py`: the stdlib check, `python3 -I check_sprites.py`.

## Format

- Frame: 96x96, drawn on a 48x48 grid and scaled 2x (nearest), with an opaque
  background in the palette's `background` colour.
- Frames per state: 4 in every state, played left to right on a loop.
  - idle: a slow bob and a blink on frame 4.
  - listen: perks up, eyes wide, sound arcs that pulse 1, 2, 2, 1.
  - think: thought dots light one by one; Claude's eyes wander, Codex shows a spinner,
    Grokbot's visor scans left to right.
  - speak: the mouth cycles open, half and closed with a small bounce.
- Palette: every channel sits on the RGB565 grid (red and blue multiples of 8, green
  multiples of 4), so packing to RGB565 and shifting back gives the same colour and the
  screen shows exactly these colours. The PNGs are 8-bit indexed with this palette.

## The mascots

All three are original characters drawn for this repo. No vendor logo, mark or
trademark is copied or traced, and no third-party asset is reused, so there is no
licence to cite.

- **Claude**: a round clay-coloured bean with a two-leaf sprout, in the warm
  terracotta and cream of a friendly writing companion.
- **Codex**: a small CRT terminal whose face is the screen, with a `>_` prompt and a
  blinking cursor in green phosphor, for the coding agent.
- **Grokbot**: a slate dome robot with one wide purple visor and a lopsided grin, for
  the sci-fi agent with a wry streak.
