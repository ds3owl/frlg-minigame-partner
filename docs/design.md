# Design

Pokemon Jump and Dodrio Berry Picking (Two Island) need two to five wireless players. FRLG
Minigames lets one Switch play them with the PC supplying the other players. The PC plays by the
game's own rules: it never writes a score or record, and the Switch player's results come from
their own play.

## The link

The Switch release runs the GBA ROM; its wireless adapter (RFU) traffic is carried over local
wireless (LDN) and Pia. `frlg_minigames/net/` is that stack: LDN through the ESP32 board
(`net/ldn/esp32*.py`, `third_party/LDN`), Pia and its reliable layer, the GBA adapter's RFU
protocol (`net/gba/`), and FireRed/LeafGreen's link layer (`net/frlg/link/`).

Two facts from the decompilation make extra players cheap:

- A child takes the player count and its own multiplayer id from the leader's `SEND_PLAYER_IDS`
  alone [link_rfu_2.c:1138-1142, 1058-1066].
- A child copies all five rows of the leader's command table into `gRecvCmds`
  [link_rfu_2.c:965-982] and handles each row by its opcode [link_rfu_2.c:1125-1215]; no row is
  checked against the radio's connection bitmap.

So when the PC is the leader, a **virtual player** is one more row (multiplayer id 2..4) in the
table the leader broadcasts each VBlank (`virtual_player.py`; the rows reach the table through
`MinigameRFULeader` in `session.py`). Only the Switch is on the radio.

| minigame | players | who leads | PC role | files |
|---|---|---|---|---|
| Pokemon Jump | 2 | the Switch | one member (wireless child) | `pokejump_member.py`, `pokejump_model.py`, `pokejump_app.py` |
| Dodrio Berry Picking | 3 | the PC | leader + one virtual member | `berrypick_host.py`, `berrypick_game.py`, `berrypick_app.py` |

Packets ride `RFUCMD_SEND_PACKET` (12 bytes, `packets.py`).

## Playing by the rules

- **Pokemon Jump** (`pokejump_model.py`): the member's own vine (speed table, RNG
  `ISO_RANDOMIZE1` seeded from the leader's `comm.data`, per-round timer sync), jump arc and hit
  test are ported from `pokemon_jump.c`. The bot presses A on a frame it chose by simulating those
  rules ahead (this swing and the next) and reports only what the rules give. The streak
  (`jumpsInRow`) is counted by the Switch, the leader. The bot's Pokemon is Charmander
  (`JUMP_TYPE_FAST`): the planning clears the stage-3 top speed, 82, only with a fast jumper.
- **Dodrio Berry Picking** (`berrypick_game.py`): the leader's berry spawn, fall, pick, eat, miss,
  difficulty and results logic are ported line by line, including the retail bugs
  (`AllPlayersReadyToStart`, u8 wrap-arounds). The console's record is copied from the leader's
  results block, so this is the code that decides it. Bots play the leader's and the virtual
  Dodrio through the same inputs a console has (the leader's four-frame input queue included). A
  berry in a column the Switch player reaches is never taken by the leader's Dodrio, and by the
  virtual member only when, run ahead on a copy by the leader's own rules, a Switch press on any
  of the next frames could no longer land on it.

## The 11-minute freeze (fixed)

A Switch-led group froze about 11 minutes after the PC joined (2026-10-15, twice). The member
numbered its Pia packets per destination variable, its property acks to destination 0 included,
while the host checks every destination other than 1 against one counter and accepts an id only if
`(int16)(id - last) >= 1`; id 0 passes and stores nothing. At ~50 data packets a second the data
ids led the ack ids by 32768 after ~683 s; the next ack then passed, set the host's counter low,
and every data packet after it read as stale. The property ack now carries id 0, like every other
establishing frame (`net/ldn/pia_connect.py`; `tests/test_long_session.py` models both).

## Tests

- `tests/test_frlg_minigames.py`: both engines against models of the other console: slow and
  lossy standbys, the three-round link save, game over, play again, close-link, a whole Dodrio game
  whose record equals what the console saw itself eat, the bots' sharing rule.
- `tests/test_frlg_minigame_oracle.py`: the original functions cut out of the decomp verbatim,
  compiled with gcc and compared frame by frame with the Python port.
- `tests/test_frlg_minigame_console.py`: the PC side on the real host or joiner stack, over an
  in-memory LDN wire, against a console built from the decomp itself (`tests/rig/console`).
- `tests/test_long_session.py`, `tests/test_apps.py`: the 11-minute fix and the app runners.

The oracle and console tests need gcc and pret/pokefirered (`POKEFIRERED_SRC=.../pokefirered/src`
or a sibling `pokefirered` checkout); without them they are skipped. `tools/gen_berry_tables.py`
regenerates `berrypick_tables.py` from the decomp.
