"""Differential tests: the original C from pret/pokefirered, cut out verbatim and compiled, runs
next to the Python port on the same inputs; every frame's state must match.

Skipped without a C compiler or a pokefirered checkout (POKEFIRERED_SRC=.../pokefirered/src)."""

import os
import random

import pytest

from tests.oracle import c_extract as cx

SRC = cx.decomp_src()
pytestmark = pytest.mark.skipif(SRC is None or not cx.have_compiler(),
                                reason="needs pret/pokefirered src and a C compiler")

PRELUDE = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32;
typedef signed char s8; typedef short s16; typedef int s32;
typedef u8 bool8; typedef u16 bool16; typedef u32 bool32;
#define TRUE 1
#define FALSE 0
#define ARRAY_COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define MAX_RFU_PLAYERS 5
#define RAND_MULT 1103515245
#define ISO_RANDOMIZE1(val)(RAND_MULT * (val) + 24691)
#define EWRAM_DATA
#define ALIGNED(n) __attribute__((aligned(n)))
typedef void (*MainCallback)(void);
struct Sprite { int unused; };
static int gPressA;
#define JOY_NEW(button) (gPressA)
#define A_BUTTON 1
static void PlaySE(int s) { (void)s; }
"""


def _read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


# --- Pokemon Jump: the member's own vine and jump --------------------------------------------

JUMP_FUNCS = ["InitVineState", "ResetVineState", "UpdateVineState", "GetVineSpeed",
              "UpdateVineSpeed", "PokeJumpRandom", "ResetVineAfterHit", "IsGameOver",
              "ResetPlayersJumpStates", "IsPlayersMonState", "SetMonStateJump",
              "SetMonStateHit", "SetMonStateNormal", "UpdateJump", "UpdateVineHitStates",
              "HandleSwingRound", "AllowVineUpdates", "SetLinkTimeInterval",
              "ResetPlayersForNewGame"]


def _jump_oracle_source():
    c = _read("pokemon_jump.c")
    head = c[c.index("#define JUMP_PEAK"):c.index("#define LINK_TIMER_STOPPED")] \
        + "#define LINK_TIMER_STOPPED 0x1111\n"
    structs = "\n".join(cx.block(c, p) for p in (
        r"^struct PokemonJump_MonInfo$", r"^struct PokemonJump_Player$",
        r"^struct PokemonJumpGfx$", r"^struct PokemonJump_CommData$", r"^struct PokemonJump$"))
    tables = "\n".join(cx.block(c, p) for p in (
        r"^static const s8 sJumpOffsets", r"^static const u16 sVineBaseSpeeds",
        r"^static const u16 sVineSpeedDelays"))
    bodies = [cx.function(c, n) for n in JUMP_FUNCS]
    protos = "\n".join(b.split("\n")[0] + ";" for b in bodies)
    funcs = "\n".join(bodies)
    main = r"""
static struct PokemonJump sJ;
static void SetMonSpriteY(u32 id, s16 y) { (void)id; (void)y; }
/* HandleMonState for the member's own player only: the jump branch of :2253-2290. */
static void OwnMonState(void) {
    struct PokemonJump_Player *p = &sPokemonJump->players[0];
    if (p->monState == MONSTATE_JUMP) {
        if (p->prevMonState != MONSTATE_JUMP || p->jumpTimeStart != sPokemonJump->jumpTimeStarts[0]) {
            p->prevMonState = MONSTATE_JUMP;
            p->jumpOffsetIdx = INT_MAX;
            sPokemonJump->jumpTimeStarts[0] = p->jumpTimeStart;
        }
        UpdateJump(0);
    } else if (p->monState == MONSTATE_HIT) {
        if (p->prevMonState != MONSTATE_HIT) p->prevMonState = MONSTATE_HIT;
    }
}
int main(void) {
    unsigned seed, type; char op;
    sPokemonJump = &sJ;
    memset(&sJ, 0, sizeof sJ);
    if (scanf("%u %u", &seed, &type) != 2) return 1;
    sJ.numPlayers = 1; sJ.multiplayerId = 0; sJ.player = &sJ.players[0];
    sJ.players[0].monJumpType = type; sJ.players[0].jumpOffsetIdx = INT_MAX;
    InitVineState();
    sJ.rngSeed = seed;
    AllowVineUpdates(); ResetVineState();
    while (scanf(" %c", &op) == 1) {
        int r = -1;
        if (op == 'A' || op == 'N') {          /* one GameRound_Member frame + UpdateGame */
            gPressA = (op == 'A');
            if (!HandleSwingRound()) r = 1;
            else if (UpdateVineHitStates()) r = 0;
            else { ResetVineAfterHit(); r = 2; }
            OwnMonState();
        } else if (op == 'W') {                 /* WaitRound_Member start + one frame */
            unsigned t; scanf("%u", &t);
            ResetPlayersJumpStates(); sJ.vineTimer = t; OwnMonState(); r = 3;
        } else if (op == 'R') {                 /* RESET_GAME then GAME_INTRO's vine reset */
            unsigned s2; scanf("%u", &s2);
            ResetPlayersForNewGame(); sJ.helperState = 0; sJ.rngSeed = s2;
            AllowVineUpdates(); ResetVineState(); r = 4;
        }
        printf("%d %u %u %u %u %d %u %u %u %u %u %u %d %d %u %u %u\n", r, sJ.vineTimer,
               sJ.vineStateTimer, sJ.vineState, sJ.prevVineState, sJ.vineSpeed, sJ.rngSeed,
               sJ.helperState, sJ.players[0].monState, sJ.players[0].prevMonState,
               (unsigned)sJ.players[0].jumpState, sJ.players[0].jumpTimeStart,
               sJ.players[0].jumpOffset, sJ.players[0].jumpOffsetIdx,
               (unsigned)sJ.ignoreJumpInput, (unsigned)sJ.gameOver, sJ.vineSpeedStage);
    }
    return 0;
}
"""
    return (PRELUDE + head + "\n" + structs + "\n" + "static struct PokemonJump *sPokemonJump;\n"
            + tables + "\n" + protos + "\n" + "static void SetMonSpriteY(u32 id, s16 y);\n"
            + funcs + main)


def _python_jump_rows(seed, jump_type, script):
    from frlg_minigames.pokejump_model import JumpModel
    m = JumpModel(jump_type)
    m.init_vine_state()
    m.rng_seed = seed
    m.allow_vine_updates = True
    m.reset_vine_state()
    rows = []
    for op in script:
        if op[0] in "AN":
            res = m.game_round_frame(op[0] == "A")
            r = {"continue": 0, "round_end": 1, "hit": 2}[res]
        elif op[0] == "W":
            m.jump_state = 0
            m.vine_timer = op[1]
            m.update_own_jump()
            r = 3
        else:
            _reset_for_new_game(m, op[1])
            r = 4
        rows.append((r, m.vine_timer, m.vine_state_timer & 0xFFFF, m.vine_state,
                     m.prev_vine_state, m.vine_speed, m.rng_seed, m.helper_state, m.mon_state,
                     m.prev_mon_state, m.jump_state, m.jump_time_start & 0xFFFF, m.jump_offset,
                     m.jump_offset_idx, m.ignore_jump_input, int(m.game_over),
                     m.vine_speed_stage))
    return rows


def _reset_for_new_game(m, seed):
    m.reset_for_new_game(seed)          # the engine's own code path


def _jump_script(rng, seed, jump_type, frames, blunder_rate):
    """Play with the bot, blundering now and then; after a hit start a new game. The script is
    made on the Python model, then both sides replay it."""
    from frlg_minigames.pokejump_model import JumpBot, JumpModel
    m = JumpModel(jump_type)
    m.init_vine_state()
    m.rng_seed = seed
    m.allow_vine_updates = True
    m.reset_vine_state()
    bot = JumpBot()
    script = []
    for _ in range(frames):
        if rng.random() < blunder_rate:
            press = rng.random() < 0.5
        else:
            press = bot.should_jump(m)
        op = ("A",) if press else ("N",)
        script.append(op)
        result = m.game_round_frame(press)
        if result == "round_end":
            t = rng.randrange(0, 0x10000) if rng.random() < 0.05 else m.vine_timer
            script.append(("W", t))
            m.jump_state = 0
            m.vine_timer = t
            m.update_own_jump()
        elif result == "hit":
            new_seed = rng.randrange(0, 1 << 32)
            script.append(("R", new_seed))
            _reset_for_new_game(m, new_seed)
            bot.reset()
    return script


@pytest.mark.parametrize("seed,jump_type,blunder_rate", [
    (0x1234, 0, 0.0005), (0xBEEF, 1, 0.004), (7, 2, 0.0), (0xFFFFFFFF, 1, 0.01), (99, 0, 0.0)])
def test_pokejump_vine_and_jump_match_the_original_c_frame_by_frame(seed, jump_type,
                                                                   blunder_rate):
    rng = random.Random(seed ^ jump_type)
    script = _jump_script(rng, seed, jump_type, 40000, blunder_rate)
    stdin = f"{seed} {jump_type}\n" + "\n".join(
        op[0] if len(op) == 1 else f"{op[0]} {op[1]}" for op in script) + "\n"
    c_rows = [tuple(int(x) for x in line.split())
              for line in cx.compile_and_run(_jump_oracle_source(), stdin).splitlines()]
    py_rows = _python_jump_rows(seed, jump_type, script)
    assert len(c_rows) == len(py_rows) == len(script)
    for frame, (c_row, py_row) in enumerate(zip(c_rows, py_rows)):
        assert c_row == py_row, (frame, script[frame], c_row, py_row)
    # the scripts must exercise round ends and the faster speed stages, or the comparison is weak
    results = {row[0] for row in c_rows}
    assert {0, 1, 3}.issubset(results)
    assert max(row[16] for row in c_rows) >= 1
    if blunder_rate:
        assert 2 in results


# --- Dodrio Berry Picking: the leader's game ------------------------------------------------

BERRY_FUNCS = ["InitDodrioGame", "GetActiveBerryColumns", "InitFirstWaveOfBerries",
               "RecvLinkData_Gameplay", "RecvLinkData_ReadyToEnd", "PlayGame_Leader",
               "WaitEndGame_Leader", "UpdateFallingBerries", "HandlePickBerries", "TryPickBerry",
               "IncrementBerryResult", "UpdateBerriesPickedInRow", "SetMaxBerriesPickedInRow",
               "TryIncrementDifficulty", "GetPlayerIdAtColumn", "GetNewBerryId",
               "GetNewBerryIdByDifficulty", "IncrementWithLimit", "UpdatePickStateQueue",
               "ReadyToEndGame_Leader", "SetGameFunc", "SendLinkData_Leader", "SetRandomPrize"]
COMM_FUNCS = ["SendPacket_GameState", "RecvPacket_GameState", "RecvPacket_PickState",
              "RecvPacket_ReadyToEnd"]


def _berry_oracle_source():
    c = _read("dodrio_berry_picking.c")
    comm = _read("dodrio_berry_picking_comm.c")
    with open(os.path.join(SRC, "..", "include", "dodrio_berry_picking.h"), encoding="utf-8") as f:
        header = f.read()
    header = header[header.index("#define NUM_BERRY_COLUMNS"):header.index("// dodrio_berry_picking.c")]
    defines = c[c.index("#define MAX_SCORE"):c.index("enum {\n    BG_INTERFACE")]
    enums = c[c.index("enum {\n    FUNC_INTRO"):c.index("// Colors for status bar squares")]
    extra = c[c.index("#define NUM_STATUS_SQUARES"):c.index("#define NUM_STATUS_SQUARES") + 40]
    extra = extra.split("\n")[0] + "\n#define PLAYER_NONE 0xFF\n#define BG_SCREEN_SIZE 0x800\n"
    structs = "\n".join(cx.block(c, p) for p in (
        r"^struct DodrioGame_Gfx$", r"^struct StatusBar$", r"^struct DodrioGame_MonInfo$",
        r"^struct DodrioGame_ScoreResults$", r"^struct DodrioGame$"))
    tables = "\n".join(cx.block(c, p) for p in (
        r"^static const u8 sActiveColumnMap\[", r"^static const u8 sDodrioHeadToColumnMap\[",
        r"^static const u8 sDodrioNeighborMap\[", r"^static const u8 sUnsharedColumns\[",
        r"^static const u8 sBerryFallDelays", r"^static const u8 sDifficultyThresholds",
        r"^static const u8 sPrizeBerryIds"))
    with open(os.path.join(SRC, "..", "include", "constants", "items.h")) as f:
        items = f.read()
    used = set(__import__("re").findall(r"ITEM_[A-Z0-9_]+", tables))
    item_defs = "\n".join(line for line in items.split("\n")
                           if line.startswith("#define ITEM_TO_BERRY")
                           or line.startswith("#define FIRST_BERRY_INDEX")
                           or any(line.startswith(f"#define {u} ") or line.startswith(f"#define {u}\t")
                                  for u in used | {"ITEM_CHERI_BERRY"}))
    tables = item_defs + "\n" + tables
    x_table = c[c.index("#define x 9"):c.index("#undef x") + len("#undef x")]
    comm_enum = comm[comm.index("enum {"):comm.index("struct ReadyToStartPacket")]
    comm_structs = comm[comm.index("struct ReadyToStartPacket"):comm.index("void SendPacket_ReadyToStart")]
    bodies = [cx.function(c, n) for n in BERRY_FUNCS]
    comm_bodies = []
    for n in COMM_FUNCS:
        m = comm.index("\n" + ("void " if n.startswith("Send") else "bool") )  # noqa: F841
        start = comm.index(n + "(")
        start = comm.rindex("\n", 0, start) + 1
        end = comm.index("\n}\n", start) + 3
        comm_bodies.append(comm[start:end])
    protos = "\n".join(b.split("\n")[0].rstrip() + ";" for b in bodies)
    comm_protos = []
    for b in comm_bodies:
        sig = b[:b.index("{")].strip()
        comm_protos.append(sig + ";")
    main = r"""
static u16 gRecvCmds[MAX_RFU_PLAYERS][8];
static u16 gSent[8];
static int gLeaderKey;
static u32 gRngState;
static int gNumPlayers;
static struct DodrioGame sG;
u16 Random(void) { gRngState = ISO_RANDOMIZE1(gRngState); return gRngState >> 16; }
static u8 GetMultiplayerId(void) { return 0; }
static u8 GetLinkPlayerCount(void) { return gNumPlayers; }
static void Rfu_SendPacket(void *data) { memcpy(&gSent[1], data, 12); gSent[0] = RFUCMD_SEND_PACKET; }
static void ClearRecvCommands(void) { memset(gRecvCmds, 0, sizeof gRecvCmds); }
static void HandleSound_Leader(void) {}
static u32 min(u32 a, u32 b) { return a < b ? a : b; }
int main(void) {
    unsigned n, rng, prize; int i, j;
    sGame = &sG;
    memset(&sG, 0, sizeof sG);
    if (scanf("%u %u %u", &n, &rng, &prize) != 3) return 1;
    gNumPlayers = n; gRngState = rng;
    InitDodrioGame(sGame);
    SetRandomPrize();                 /* as StartDodrioBerryPicking does [:723-748] */
    GetActiveBerryColumns(sG.numPlayers, &sG.berryColStart, &sG.berryColEnd);
    InitFirstWaveOfBerries();
    sG.funcId = FUNC_PLAY_GAME;
    for (;;) {
        unsigned key, rows[MAX_RFU_PLAYERS], row0;
        if (scanf("%u %u %u %u %u %u", &key, &rows[1], &rows[2], &rows[3], &rows[4], &row0) != 6) break;
        /* row 0: the leader's own last packet (loopback), or nothing; rows 1..4: members */
        memset(gRecvCmds, 0, sizeof gRecvCmds);
        if (row0 && gSent[0]) memcpy(gRecvCmds[0], gSent, sizeof gSent);
        for (i = 1; i < MAX_RFU_PLAYERS; i++) {
            if (!rows[i]) continue;
            gRecvCmds[i][0] = RFUCMD_SEND_PACKET;
            if (sG.funcId == FUNC_PLAY_GAME) { gRecvCmds[i][1] = 3; gRecvCmds[i][3] = rows[i]; }
            else { gRecvCmds[i][1] = 4; gRecvCmds[i][3] = 1; }
        }
        gLeaderKey = key;
        if (sG.funcId == FUNC_PLAY_GAME) { RecvLinkData_Gameplay(); PlayGame_Leader(); }
        else if (sG.funcId == FUNC_WAIT_END_GAME) { RecvLinkData_ReadyToEnd(); WaitEndGame_Leader(); }
        else break;
        memset(gSent, 0, sizeof gSent);    /* gSendCmd is cleared once sent [link_rfu_2.c:759-767] */
        SendLinkData_Leader();
        printf("%u", sG.funcId);
        for (i = 0; i < NUM_BERRY_COLUMNS; i++) printf(" %u %u %u %u %u %u %u %u", sG.player.berries.fallDist[i],
            sG.player.berries.ids[i], sG.berryState[i], sG.fallTimer[i], sG.newBerryTimer[i], sG.eatTimer[i],
            sG.playersAttemptingPick[i][0], sG.playersAttemptingPick[i][1]);
        for (i = 0; i < MAX_RFU_PLAYERS; i++) {
            printf(" %u %u %u %u %u %u %u %u", sG.inputState[i], sG.inputDelay[i], sG.players[i].comm.pickState,
                   sG.players[i].comm.ateBerry, sG.players[i].comm.missedBerry, sG.difficulty[i],
                   sG.berriesEaten[i], (unsigned)sG.readyToEnd[i]);
            for (j = 0; j < NUM_BERRY_IDS; j++) printf(" %u", sG.berryResults[i][j]);
        }
        printf(" %u %u %u %u %u", sG.numGraySquares, (unsigned)sG.berriesFalling, (unsigned)sG.allReadyToEnd,
               sG.berriesPickedInRow, sG.maxBerriesPickedInRow);
        for (i = 0; i < 4; i++) printf(" %u", sG.pickStateQueue[i]);
        for (i = 1; i < 7; i++) printf(" %u", gSent[i]);
        printf("\n");
    }
    if (sG.funcId == FUNC_INIT_RESULTS) {
        /* the berryResults bytes InitResults_Leader block-sends [:1076-1080] */
        const u8 *b = (const u8 *)sG.berryResults;
        printf("R");
        for (i = 0; i < (int)sizeof(sG.berryResults); i++) printf(" %u", b[i]);
        printf("\n");
    }
    return 0;
}
"""
    prelude = PRELUDE.replace("#define JOY_NEW(button) (gPressA)",
                              "#define JOY_NEW(button) (gLeaderKey == (button))") \
        .replace("#define A_BUTTON 1", "#define DPAD_RIGHT 1\n#define DPAD_UP 2\n#define DPAD_LEFT 3\n"
                 "#define UBFIX\n#define RFUCMD_MASK 0xFF00\n#define RFUCMD_SEND_PACKET 0x2F00\n"
                 "#define SE_BALLOON_RED 0\n#define SE_SUCCESS 0\n#define SE_BOO 0\n"
                 "static int gLeaderKey;\nu16 Random(void);\nstatic void ClearRecvCommands(void);\n"
                 "static void Rfu_SendPacket(void *data);\nstatic u8 GetMultiplayerId(void);\n"
                 "static u8 GetLinkPlayerCount(void);\nstatic void HandleSound_Leader(void);\n"
                 "static u32 min(u32 a, u32 b);\nstatic u16 gRecvCmds[5][8];\n")
    main = main.replace("static u16 gRecvCmds[MAX_RFU_PLAYERS][8];\n", "") \
        .replace("static int gLeaderKey;\n", "")
    return (prelude + "typedef void (*MainCallback)(void);\n".replace("typedef void (*MainCallback)(void);\n", "")
            + header + defines + extra + enums + structs + "\n" + x_table + "\n" + tables + "\n"
            + comm_enum + comm_structs + "\nstatic struct DodrioGame *sGame;\n" + protos + "\n"
            + "\n".join(comm_protos) + "\n" + "\n".join(bodies) + "\n" + "\n".join(comm_bodies)
            + main)


class _IsoRng:
    """The GBA's Random() [random.c]: ISO_RANDOMIZE1, high half."""

    def __init__(self, state):
        self.state = state & 0xFFFFFFFF

    def randrange(self, n):
        return 0          # SetRandomPrize; the test overwrites the prize on both sides

    def getrandbits(self, bits):
        self.state = (1103515245 * self.state + 24691) & 0xFFFFFFFF
        return (self.state >> 16) & ((1 << bits) - 1)


def _python_berry_rows(n, rng_state, prize, script):
    from frlg_minigames import berrypick_game as bg
    from frlg_minigames import packets as pk
    g = bg.DodrioLeaderGame(n, rng=_IsoRng(rng_state))      # SetRandomPrize inside
    g.init_first_wave()
    sent = None
    rows = []
    key_to_pick = {0: bg.PICK_NONE, 1: bg.PICK_RIGHT, 2: bg.PICK_MIDDLE, 3: bg.PICK_LEFT}
    for key, members, row0 in script:
        if g.func not in (bg.FUNC_PLAY_GAME, bg.FUNC_WAIT_END_GAME):
            break
        picks = {i: p for i, p in enumerate(members, start=1) if p}
        ready = [i for i, p in enumerate(members, start=1) if p]
        row0_packet = bool(row0) and sent is not None
        packet = g.leader_frame(sent, picks, key_to_pick[key],
                                row0_is_packet=row0_packet, ready_ids=ready)
        sent = packet
        row = [g.func]
        for i in range(bg.NUM_BERRY_COLUMNS):
            row += [g.fall_dist[i], g.ids[i], g.berry_state[i], g.fall_timer[i] & 0xFF,
                    g.new_berry_timer[i] & 0xFF, g.eat_timer[i], *g.players_attempting_pick[i]]
        for i in range(bg.MAX_RFU_PLAYERS):
            c = g.comm[i]
            row += [g.input_state[i], g.input_delay[i], c.pick_state, int(c.ate_berry),
                    int(c.missed_berry), g.difficulty[i], g.berries_eaten[i],
                    int(g.ready_to_end[i])]
            row += list(g.berry_results[i])
        row += [g.num_gray_squares, int(g.berries_falling), int(g.all_ready_to_end),
                g.berries_picked_in_row, g.max_berries_picked_in_row]
        row += list(g.pick_state_queue)
        row += pk.words_from_bytes(packet) if packet is not None else [0] * 6
        rows.append(tuple(row))
    return rows


def _python_results_block(n, rng_state, script):
    from frlg_minigames import berrypick_game as bg
    g = bg.DodrioLeaderGame(n, rng=_IsoRng(rng_state))
    g.init_first_wave()
    sent = None
    key_to_pick = {0: bg.PICK_NONE, 1: bg.PICK_RIGHT, 2: bg.PICK_MIDDLE, 3: bg.PICK_LEFT}
    for key, members, row0 in script:
        if g.func not in (bg.FUNC_PLAY_GAME, bg.FUNC_WAIT_END_GAME):
            break
        picks = {i: p for i, p in enumerate(members, start=1) if p}
        sent = g.leader_frame(sent, picks, key_to_pick[key],
                              row0_is_packet=bool(row0) and sent is not None,
                              ready_ids=[i for i, p in enumerate(members, start=1) if p])
    return g.results_block()


def _berry_script(n, seed, frames, neglect=0.15):
    """Inputs made by playing the Python model with its bots plus noise: wrong-time presses,
    two players on one berry, missed reactions, a few frames without row 0."""
    import copy  # noqa: F401
    from frlg_minigames import berrypick_game as bg
    rng = random.Random(seed)
    g = bg.DodrioLeaderGame(n, rng=_IsoRng(seed))
    g.init_first_wave()
    bots = {i: bg.BerryBot(g, i, lag=(5 if i == 0 else 1)) for i in range(n)}
    pick_to_key = {bg.PICK_NONE: 0, bg.PICK_RIGHT: 1, bg.PICK_MIDDLE: 2, bg.PICK_LEFT: 3}
    script, sent = [], None
    for _ in range(frames):
        if g.func not in (bg.FUNC_PLAY_GAME, bg.FUNC_WAIT_END_GAME):
            break
        members = [0, 0, 0, 0]
        if g.func == bg.FUNC_PLAY_GAME:
            for i in range(1, n):
                p = bots[i].choose()
                if rng.random() < neglect:
                    p = bg.PICK_NONE
                if rng.random() < 0.01:
                    p = rng.choice([bg.PICK_LEFT, bg.PICK_MIDDLE, bg.PICK_RIGHT])
                members[i - 1] = p
            key = pick_to_key[bots[0].choose()] if rng.random() > max(0.2, neglect) \
                else rng.choice([0, 0, 0, 0, 1, 2, 3])
        else:
            key = 0
            members = [1 if (i < n and rng.random() < 0.5) else 0 for i in range(1, 5)]
        row0 = 0 if rng.random() < 0.01 else 1
        script.append((key, members, row0))
        picks = {i: p for i, p in enumerate(members, start=1) if p}
        sent = g.leader_frame(sent, picks, {0: 0, 1: 1, 2: 2, 3: 3}[key],
                              row0_is_packet=bool(row0) and sent is not None,
                              ready_ids=[i for i, p in enumerate(members, start=1) if p])
    return script


# 11 scenarios: normal play, heavy neglect and total neglect, so that every column's miss charge
# and the end of the game are exercised.
@pytest.mark.parametrize("n,seed,neglect", [
    (3, 1, 0.15), (3, 77, 0.15), (4, 5, 0.15), (5, 9, 0.15), (5, 123, 0.15),
    (3, 2, 0.97), (4, 3, 0.97), (5, 4, 0.97), (5, 6, 0.6), (4, 7, 1.0), (5, 8, 1.0)])
def test_dodrio_leader_matches_the_original_c_frame_by_frame(n, seed, neglect):
    script = _berry_script(n, seed, 30000, neglect)
    prize = seed % 10
    stdin = f"{n} {seed} {prize}\n" + "\n".join(
        f"{k} {m[0]} {m[1]} {m[2]} {m[3]} {r}" for k, m, r in script) + "\n"
    out = cx.compile_and_run(_berry_oracle_source(), stdin)
    lines = out.splitlines()
    results_line = [line for line in lines if line.startswith("R")]
    c_rows = [tuple(int(x) for x in line.split()) for line in lines if not line.startswith("R")]
    py_rows = _python_berry_rows(n, seed, prize, script)
    assert len(c_rows) == len(py_rows) > 300
    for frame, (c_row, py_row) in enumerate(zip(c_rows, py_rows)):
        if c_row != py_row:
            diff = [i for i, (a, b) in enumerate(zip(c_row, py_row)) if a != b]
            pytest.fail(f"frame {frame} script {script[frame]}: fields {diff[:8]} "
                        f"C {[c_row[i] for i in diff[:8]]} Py {[py_row[i] for i in diff[:8]]}")
    # the game must have ended and been scored, or the comparison is weak
    assert c_rows[-1][0] in (5, 11)
    if c_rows[-1][0] == 5:
        # the results block, byte for byte, is what the original leader would send
        c_block = bytes(int(x) for x in results_line[0].split()[1:])
        assert _python_results_block(n, seed, script) == c_block
