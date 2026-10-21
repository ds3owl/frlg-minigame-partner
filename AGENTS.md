# For AI coding tools

This repository plays two FireRed/LeafGreen (Nintendo Switch) wireless minigames with the PC
filling the other players' places, through an ESP32 board on USB. Windows is the tested platform.

## Run from source

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m frlg_minigames          REM the desktop app
```

Without a window, the same steps from the terminal (`frlg_minigames/cli.py`):

```bat
.venv\Scripts\python -m frlg_minigames.cli ports                     REM find the board, e.g. COM7
.venv\Scripts\python -m frlg_minigames.cli prepare --port COM7       REM first time: write the firmware
.venv\Scripts\python -m frlg_minigames.cli jump  --port COM7 --keys PATH\prod.keys --log jump.log
.venv\Scripts\python -m frlg_minigames.cli berry --port COM7 --keys PATH\prod.keys --log berry.log
```

What the person does on the Switch:

- `jump`: Two Island → Joyful Game Corner → Pokémon Jump attendant → pick a Pokémon →
  BECOME LEADER. The PC (Ash) joins; then they start the game and play their own Pokémon.
- `berry`: start the command first. Then Dodrio Berry Picking attendant → pick a Dodrio →
  JOIN GROUP → choose Ash.

Ctrl+C stops. Status lines print to stdout; `--log` keeps the detailed log for debugging.

## Notes

- Never commit or print the contents of `prod.keys`.
- Fast jumpers are easiest for the person when the vine speeds up: Charmander, Caterpie, Metapod,
  Weedle, Kakuna, Rattata, Raticate, Paras, Krabby.
- Tests: `.venv\Scripts\python -m pytest -q tests`. The tests that compile the original game code
  need gcc and a pret/pokefirered checkout next to this folder (or `POKEFIRERED_SRC`).
- Layout: `frlg_minigames/` the minigames and the app, `frlg_minigames/net/` the wireless link,
  `third_party/LDN/` the LDN library, `firmware/` the board image, `docs/design.md` the design.
