"""Build a "fake console": the original FireRed/LeafGreen link code and minigame code from
pret/pokefirered compiled for the host, with graphics, sound and save I/O stubbed, driven one
VBlank at a time through ctypes (tests/rig/console/console.py).

Original, compiled as is: src/link_rfu_2.c, link_rfu_3.c, link.c, task.c, random.c,
dodrio_berry_picking.c, dodrio_berry_picking_comm.c, pokemon_jump.c; save.c's Task_LinkFullSave
(cut verbatim). Ours: the per-VBlank driver standing in for RfuMain1/RfuMain2 above librfu, the
union-room steps between the player exchange and the minigame, and the stubs."""

import os
import re
import shutil
import subprocess

from tests.oracle import c_extract as cx

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = ["src/link_rfu_3.c", "src/link.c", "src/random.c",
           "src/dodrio_berry_picking_comm.c", "src/sprite.c"]

# task.c keeps a pointer in two s16 task fields; a 64-bit host pointer does not fit, so the
# wrapper also keeps the full value (the 32-bit fields are still written as the original does).
TASK_C = r'''
#define FC_TASK_IMPL
#define GetWordTaskArg GetWordTaskArg_orig
#define SetWordTaskArg SetWordTaskArg_orig
#include "@@ROOT@@/src/task.c"
#undef GetWordTaskArg
#undef SetWordTaskArg
static unsigned long sFcWordArgs[NUM_TASKS][16];
void SetWordTaskArg(u8 taskId, u8 dataElem, unsigned long value)
{
    SetWordTaskArg_orig(taskId, dataElem, value);
    if (dataElem <= 14) sFcWordArgs[taskId][dataElem] = value;
}
unsigned long GetWordTaskArg(u8 taskId, u8 dataElem)
{
    unsigned long low = GetWordTaskArg_orig(taskId, dataElem);
    if (dataElem <= 14 && (sFcWordArgs[taskId][dataElem] & 0xFFFFFFFFul) == low)
        return sFcWordArgs[taskId][dataElem];
    return low;
}
'''

JUMP_C = r'''
#include "@@ROOT@@/src/pokemon_jump.c"
/* What the Switch player watches on the leader's own screen, for the scripted player. */
void fc_jump_state(int *out)
{
    struct PokemonJump *j = sPokemonJump;
    if (j == NULL || j->player == NULL) { out[0] = -1; return; }
    out[0] = j->comm.funcId; out[1] = j->vineState; out[2] = j->prevVineState;
    out[3] = j->vineStateTimer; out[4] = j->vineSpeed; out[5] = j->vineSpeedAccel;
    out[6] = j->vineTimer; out[7] = j->rngSeed; out[8] = j->nextVineSpeed;
    out[9] = j->vineSpeedStage; out[10] = j->vineBaseSpeedIdx; out[11] = j->vineSpeedDelay;
    out[12] = j->atMaxSpeedStage; out[13] = j->ignoreJumpInput; out[14] = j->gameOver;
    out[15] = j->allowVineUpdates; out[16] = j->helperState; out[17] = j->funcActive;
    out[18] = j->player->monState; out[19] = j->player->prevMonState;
    out[20] = j->player->jumpState; out[21] = j->player->jumpTimeStart;
    out[22] = j->player->jumpOffset; out[23] = j->player->jumpOffsetIdx;
    out[24] = j->player->monJumpType; out[25] = j->comm.jumpsInRow; out[26] = j->numPlayers;
    out[27] = j->isLeader; out[28] = (int)j->comm.jumpScore;
    if (sPokemonJumpGfx != NULL) {
        out[29] = sPokemonJumpGfx->monSprites[0] != NULL;
        out[30] = sPokemonJumpGfx->monSprites[1] != NULL;
    }
    out[31] = j->monInfo[1].species;
}
'''

# The minigame files are compiled inside a wrapper so the driver can reach their statics. On the
# GBA a write through a still-NULL graphics pointer lands in the BIOS region and is ignored
# (Task_CommunicateMonInfo calls SetGfxFuncById before InitGameGfx sets sGfx
# [dodrio_berry_picking.c:1485, 818]); here the pointer starts at a scratch struct instead.
DODRIO_C = r'''
#include "@@ROOT@@/src/dodrio_berry_picking.c"
static struct DodrioGame_Gfx sFcScratchGfx;
void fc_dodrio_prime(void) { if (sGfx == NULL) sGfx = &sFcScratchGfx; }
/* what the Switch player sees: the play-again prompt and its cursor [Msg_WantToPlayAgain] */
int fc_dodrio_in_prompt(void) { return sGfx != NULL && sGfx->func == Msg_WantToPlayAgain; }
int fc_dodrio_cursor(void) { return sGfx != NULL ? sGfx->cursorSelection : 0; }
'''



def decomp_root():
    src = cx.decomp_src()
    return None if src is None else os.path.dirname(src)


def _shadow_headers(root, out_dir):
    """Copies of gba/io_reg.h and gba/defines.h whose I/O, palette, VRAM and OAM addresses point
    at host memory: the game writes registers (REG_IME around queue updates) and VRAM directly."""
    shadow = os.path.join(out_dir, "shadow")
    if os.path.isdir(shadow):
        shutil.rmtree(shadow)
    # the whole include tree: a quoted #include searches the including file's own directory
    # first, so only a complete copy shadows gba/*.h for every file
    shutil.copytree(os.path.join(root, "include"), shadow)
    subs = {
        "io_reg.h": [("#define REG_BASE 0x4000000",
                      "extern unsigned char gFcIo[];\n#define REG_BASE ((unsigned long)gFcIo)")],
        "defines.h": [("#define PLTT          0x5000000",
                       "extern unsigned char gFcPltt[];\n#define PLTT ((unsigned long)gFcPltt)"),
                      ("#define VRAM      0x6000000",
                       "extern unsigned char gFcVram[];\n#define VRAM ((unsigned long)gFcVram)"),
                      ("#define OAM      0x7000000",
                       "extern unsigned char gFcOam[];\n#define OAM ((unsigned long)gFcOam)")],
        # MODERN=1 (needed to build with a host compiler) would also switch on BUGFIX, the
        # decomp's corrected branches; the ROM has none of them, so keep only UBFIX (guards for
        # out-of-bounds accesses that the GBA shrugs off and a host process would not).
        "../config.h": [("#ifndef BUGFIX\n#define BUGFIX\n#endif // BUGFIX\n", "")],
        "../task.h": [("u32 GetWordTaskArg(u8 taskId, u8 dataElem);",
                       "#ifdef FC_TASK_IMPL\nu32 GetWordTaskArg(u8 taskId, u8 dataElem);\n"
                       "#else\nunsigned long GetWordTaskArg(u8 taskId, u8 dataElem);\n#endif")],
    }
    for name, pairs in subs.items():
        with open(os.path.join(root, "include", "gba", name), encoding="utf-8") as f:
            text = f.read()
        for old, new in pairs:
            if old not in text:
                raise RuntimeError(f"{name}: {old!r} not found")
            text = text.replace(old, new)
        with open(os.path.join(shadow, "gba", name), "w") as f:
            f.write(text)
    return shadow


SHADOW = None


def cflags(root):
    inc = SHADOW or f"{root}/include"
    return ["-w", "-g", "-fPIC", "-fcommon", "-O1", "-iquote", inc, "-iquote",
            f"{root}/gflib", "-DMODERN=1", "-DREVISION=10", "-DFIRERED=1", "-DENGLISH=1",
            "-D_(x)=x", "-DINCBIN_U8(...)={0}", "-DINCBIN_U16(...)={0}",
            "-DINCBIN_U32(...)={0}"]


ENV_C = r'''
/* Things the stubs cannot fake with a zero: memory, strings, callbacks, save data, the link save. */
#include "global.h"
#include "main.h"
#include "link.h"
#include "link_rfu.h"
#include "save.h"
#include "task.h"
#include "palette.h"
#include "sprite.h"
#include "menu.h"
#include "constants/pokemon.h"
#include "constants/global.h"
#include "characters.h"
void *calloc(unsigned long n, unsigned long size);
void free(void *p);
#ifndef EOS
#define EOS 0xFF
#endif

unsigned char gFcIo[0x1000] __attribute__((aligned(16)));
unsigned char gFcPltt[0x400] __attribute__((aligned(16)));
unsigned char gFcVram[0x18000] __attribute__((aligned(16)));
unsigned char gFcOam[0x400] __attribute__((aligned(16)));
struct Main gMain;
struct PaletteFadeControl gPaletteFade;
static struct SaveBlock2 sSave2;
static struct SaveBlock1 sSave1;
struct SaveBlock2 *gSaveBlock2Ptr = &sSave2;
struct SaveBlock1 *gSaveBlock1Ptr = &sSave1;
struct Pokemon gPlayerParty[PARTY_SIZE];
static struct RfuLinkStatus sLinkStatus;
struct RfuLinkStatus *gRfuLinkStatus = &sLinkStatus;
bool8 gSoftResetDisabled;
const u8 gGameLanguage = LANGUAGE_ENGLISH;
const u8 gGameVersion = VERSION_FIRE_RED;
u16 gSpecialVar_Result, gSpecialVar_ItemId;

void *Alloc(u32 size) { return calloc(1, size); }
void *AllocZeroed(u32 size) { return calloc(1, size); }
/* The GBA heap keeps a freed block's contents until it is reused, and the minigames read their
   state once more after FreePokemonJump [ExitGame, pokemon_jump.c:1547-1552]: never reuse. */
void Free(void *p) { (void)p; }
void SetMainCallback2(MainCallback cb) { gMain.callback2 = cb; gMain.state = 0; }
void SetVBlankCallback(IntrCallback cb) { gMain.vblankCallback = cb; }
u8 *StringCopy(u8 *dst, const u8 *src) {
    while (*src != EOS) *dst++ = *src++;
    *dst = EOS; return dst;
}
s32 StringCompare(const u8 *a, const u8 *b) {
    while (*a == *b) { if (*a == EOS) return 0; a++; b++; }
    return *a - *b;
}
s32 StringCompareN(const u8 *a, const u8 *b, u32 n) {
    while (n-- && *a == *b) { if (*a == EOS) return 0; a++; b++; }
    return n == (u32)-1 ? 0 : *a - *b;
}
u16 ReadU16(const void *p) { const u8 *b = p; return b[0] | (b[1] << 8); }
void CpuSet(const void *src, void *dst, u32 control) {
    u32 count = control & 0x1FFFFF; int fixed = control & (1 << 24); int words = control & (1 << 26);
    u32 i, unit = words ? 4 : 2;
    for (i = 0; i < count; i++)
        memcpy((u8 *)dst + i * unit, (const u8 *)src + (fixed ? 0 : i * unit), unit);
}
bool8 WaitFanfare(bool8 stop) { return TRUE; }
bool8 IsNotWaitingForBGMStop(void) { return TRUE; }     /* no music to wait for */
bool8 IsFanfareTaskInactive(void) { return TRUE; }      /* no fanfare playing */
bool32 IsMinigameCountdownRunning(void) { return FALSE; }
bool8 IsMonShiny(struct Pokemon *mon) { return FALSE; }
/* The party Pokemon the minigame reads: species for Pokemon Jump's MonInfo [InitJumpMonInfo]. */
u16 gFcSpecies = 25;
u32 gFcOtId = 0x13572468;
u32 GetMonData3(struct Pokemon *mon, s32 field, u8 *data) {
    switch (field) {
    case MON_DATA_SPECIES: case MON_DATA_SPECIES_OR_EGG: return gFcSpecies;
    case MON_DATA_OT_ID: return gFcOtId;
    case MON_DATA_SANITY_HAS_SPECIES: return 1;
    default: return 0;
    }
}
u32 GetMonData2(struct Pokemon *mon, s32 field) { return GetMonData3(mon, field, NULL); }
/* menu.c's yes/no input [Menu_ProcessInputNoWrapClearOnChoose]: A picks gFcMenuChoice
   (0 yes, 1 no), B cancels, otherwise nothing chosen. */
int gFcMenuChoice;
s8 Menu_ProcessInputNoWrapClearOnChoose(void) {
    if (gMain.newKeys & A_BUTTON) return gFcMenuChoice;
    if (gMain.newKeys & B_BUTTON) return MENU_B_PRESSED;
    return MENU_NOTHING_CHOSEN;
}

/* save.c's link save: the standby rounds are the original's; the flash writes are no-ops. */
void SaveMapView(void) {}
void SetContinueGameWarpStatusToDynamicWarp(void) {}
void ClearContinueGameWarpStatus2(void) {}
bool8 LinkFullSave_Init(void) { return FALSE; }
bool8 LinkFullSave_WriteSector(void) { return TRUE; }
bool8 LinkFullSave_ReplaceLastSector(void) { return FALSE; }
bool8 LinkFullSave_SetLastSectorSignature(void) { return FALSE; }
void svc_FinishSave(void) {}
int fc_saves_done;
@@TASK_LINK_FULL_SAVE@@

/* the console's own copy of the records [TryUpdateRecords] */
u32 fc_record_berries_picked(void) { return gSaveBlock2Ptr->berryPick.berriesPicked; }
u32 fc_record_berries_best_score(void) { return gSaveBlock2Ptr->berryPick.bestScore; }
u32 fc_record_jumps_in_row(void) { return gSaveBlock2Ptr->pokeJump.jumpsInRow; }
u32 fc_record_berries_in_row(void) { return gSaveBlock2Ptr->berryPick.berriesPickedInRow; }
void fc_set_player(const u8 *name7, u32 trainer_id) {
    int i;
    for (i = 0; i < 7 && name7[i] != EOS; i++) gSaveBlock2Ptr->playerName[i] = name7[i];
    gSaveBlock2Ptr->playerName[i] = EOS;
    for (i = 0; i < 4; i++) gSaveBlock2Ptr->playerTrainerId[i] = trainer_id >> (8 * i);
    gSaveBlock2Ptr->playerGender = 0;
}
'''

LINK_C = r'''
/* link_rfu_2.c itself, so the driver can reach its statics. */
#include "@@ROOT@@/src/link_rfu_2.c"

extern void StartDodrioBerryPicking(u16 partyId, MainCallback exitCallback);
extern void StartPokemonJump(u16 partyId, MainCallback exitCallback);
extern void fc_dodrio_prime(void);
extern int fc_saves_done;
static int sJoinState;
static int sActivity;
int fc_exited;

static void CB2_FcExit(void) { fc_exited = 1; RunTasks(); }

/* union_room.c between the player exchange and the activity, as a member: LG_STATE_MAIN waits
   for gReceivedRemoteLinkPlayers [:1257-1278], Task_RunScriptAndFadeToActivity runs one standby
   [:1975-2010], Task_StartActivity starts the minigame [:1872-1970]. */
static void CB2_FcJoin(void) {
    RunTasks();
    switch (sJoinState) {
    case 0: if (gReceivedRemoteLinkPlayers) sJoinState = 1; break;
    case 1: if (IsLinkTaskFinished()) { SetLinkStandbyCallback(); sJoinState = 2; } break;
    case 2:
        if (IsLinkTaskFinished()) {
            sJoinState = 3;
            if (sActivity == 11) StartDodrioBerryPicking(0, CB2_FcExit);
            else if (sActivity == 9) StartPokemonJump(0, CB2_FcExit);
        }
        break;
    }
}

/* A member that the leader accepted into its group: the child side of Task_PlayerExchange
   [link_rfu_2.c:462-475 creates it on RFUSTATE_CHILD_CONNECT_END]. */
void fc_init_member(int activity) {
    gWirelessCommType = 1;
    gRfu.parentChild = MODE_CHILD;
    gRfuLinkStatus->parentChild = MODE_CHILD;
    gRfuLinkStatus->connSlotFlag = 1;
    gRfu.childSlot = 0;
    gRfu.callback = NULL;
    sActivity = activity;
    fc_dodrio_prime();
    ResetTasks();
    CreateTask(Task_PlayerExchange, 5);
    gRfu.playerExchangeActive = TRUE;
    SetMainCallback2(CB2_FcJoin);
}

/* A leader whose group has one member in RFU slot 0, after START: RFUSTATE_PARENT_FINALIZE
   creates Task_PlayerExchange [link_rfu_2.c:551-560]; the member's id is linkPlayerIdx[0] = 1. */
static u16 sFcLastParentCmd[CMD_LENGTH - 1];
void fc_init_leader(int activity) {
    gWirelessCommType = 1;
    gRfu.parentChild = MODE_PARENT;
    gRfuLinkStatus->parentChild = MODE_PARENT;
    gRfu.parentSlots = 1;
    gRfu.linkPlayerIdx[0] = 1;
    gRfu.callback = NULL;
    sActivity = activity;
    fc_dodrio_prime();
    ResetTasks();
    CreateTask(Task_PlayerExchange, 5);
    gRfu.playerExchangeActive = TRUE;
    SetMainCallback2(CB2_FcJoin);
}

/* One leader VBlank above librfu: row 0 is the leader's own last command, row 1 the member's
   [ReadAllPlayerRecvCmds, link_rfu_2.c:743-767]; handle, run the link callback, send. */
void fc_parent_frame(const u16 *child_cmd, u16 *out) {
    int i, j;
    for (i = 0; i < MAX_RFU_PLAYERS; i++)
        for (j = 0; j < CMD_LENGTH - 1; j++)
            gRecvCmds[i][j] = 0;
    for (j = 0; j < CMD_LENGTH - 1; j++) {
        gRecvCmds[0][j] = sFcLastParentCmd[j];
        gRecvCmds[1][j] = child_cmd[j];
    }
    RfuHandleReceiveCommand(0);
    CallRfuFunc();
    for (j = 0; j < CMD_LENGTH - 1; j++) {
        out[j] = gSendCmd[j];
        sFcLastParentCmd[j] = gSendCmd[j];
        gSendCmd[j] = 0;
    }
}

/* RfuMain1_Child above librfu: one received table into gRecvCmds, then the command handler
   [link_rfu_2.c:965-981]. */
void fc_receive(const u16 *rows) {
    int i, j;
    for (i = 0; i < MAX_RFU_PLAYERS; i++)
        for (j = 0; j < CMD_LENGTH - 1; j++)
            gRecvCmds[i][j] = rows[i * (CMD_LENGTH - 1) + j];
    RfuHandleReceiveCommand(0);
}

/* The send half [:996-1004]: run the link callback, hand gSendCmd out, clear it. */
void fc_send(u16 *out) {
    int i;
    CallRfuFunc();
    for (i = 0; i < CMD_LENGTH - 1; i++) { out[i] = gSendCmd[i]; gSendCmd[i] = 0; }
}

/* One frame of the main loop with these keys [main.c: ReadKeys, then callback2]. */
void fc_main(u16 newKeys) {
    gMain.newKeys = newKeys; gMain.heldKeys = newKeys; gMain.newAndRepeatedKeys = newKeys;
    if (gMain.callback2) gMain.callback2();
}

int fc_received_link_players(void) { return gReceivedRemoteLinkPlayers; }
int fc_player_count(void) { return gRfu.playerCount; }
int fc_multiplayer_id(void) { return GetMultiplayerId(); }
int fc_rfu_callback_busy(void) { return gRfu.callback != NULL; }
int fc_standby_count(void) { return gRfu.resendExitStandbyCount; }
const u8 *fc_link_player_name(int i) { return gLinkPlayers[i].name; }
'''


def _task_link_full_save(root):
    with open(os.path.join(root, "src", "save.c"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^void Task_LinkFullSave\(u8 taskId\)\n\{", text, re.M)
    end = text.index("\n}\n", m.start()) + 3
    body = text[m.start():end]
    # count completed saves for the tests
    return body.replace("            gSoftResetDisabled = FALSE;",
                        "            gSoftResetDisabled = FALSE;\n            fc_saves_done++;")


# Stubs whose result the game dereferences: they hand out zeroed scratch memory instead of NULL.
POINTER_STUBS = {"GetUserWindowGraphics", "GetTextWindowPalette", "GetSpriteTileStartByTag_",
                 "DecompressAndLoadBgGfxUsingHeap", "ConvertIntToDecimalStringN",
                 "DynamicPlaceholderTextUtil_ExpandPlaceholders", "CopyItemName"}


def _nm(obj, flag):
    out = subprocess.run(["nm", flag, obj], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def build(out_dir=None):
    """-> path of the shared library, or None if the decomp or a compiler is missing."""
    root = decomp_root()
    cc = shutil.which("gcc") or shutil.which("cc")
    if root is None or cc is None:
        return None
    out_dir = out_dir or os.path.join(HERE, ".build")
    os.makedirs(out_dir, exist_ok=True)
    lib = os.path.join(out_dir, "fakeconsole.so")
    global SHADOW
    SHADOW = _shadow_headers(root, out_dir)
    flags = cflags(root)
    objs = []
    for src in SOURCES:
        obj = os.path.join(out_dir, os.path.basename(src)[:-2] + ".o")
        subprocess.run([cc, "-c", *flags, os.path.join(root, src), "-o", obj], check=True)
        objs.append(obj)
    for name, text in (("fc_dodrio.c", DODRIO_C), ("fc_task.c", TASK_C), ("fc_jump.c", JUMP_C)):
        path = os.path.join(out_dir, name)
        with open(path, "w") as f:
            f.write(text.replace("@@ROOT@@", root))
        obj = path[:-2] + ".o"
        subprocess.run([cc, "-c", *flags, path, "-o", obj], check=True)
        objs.append(obj)
    link_c = os.path.join(out_dir, "fc_link.c")
    with open(link_c, "w") as f:
        f.write(LINK_C.replace("@@ROOT@@", root))
    env_c = os.path.join(out_dir, "fc_env.c")
    with open(env_c, "w") as f:
        f.write(ENV_C.replace("@@TASK_LINK_FULL_SAVE@@", _task_link_full_save(root)))
    for c in (link_c, env_c):
        obj = c[:-2] + ".o"
        subprocess.run([cc, "-c", *flags, c, "-o", obj], check=True)
        objs.append(obj)
    defined, undefined = set(), set()
    for obj in objs:
        for line in _nm(obj, "--defined-only"):
            parts = line.split()
            if len(parts) == 3:
                defined.add(parts[2])
        for line in _nm(obj, "-u"):
            parts = line.split()
            if parts:
                undefined.add(parts[-1])
    libc = {"memcpy", "memset", "memmove", "memcmp", "calloc", "free", "malloc", "strlen",
            "printf", "puts", "abort"}
    missing = sorted(n for n in undefined - defined
                     if not n.startswith("_") and n not in libc and "@" not in n)
    stubs = ["/* zero stubs for graphics, sound, text, librfu and the rest */",
             "static char sFcScratch[1 << 16] __attribute__((aligned(16)));"]
    for name in missing:
        if name.startswith("gText_") or name.startswith("gStr"):
            stubs.append(f"const unsigned char {name}[] = {{0xFF}};")
        elif re.match(r"^g[A-Z]", name) or name in ("lman",):
            stubs.append(f"char {name}[1 << 16] __attribute__((aligned(16)));")
        elif name in POINTER_STUBS:
            stubs.append(f"void *{name}() {{ return sFcScratch; }}")
        else:
            stubs.append(f"long {name}() {{ return 0; }}")
    stubs_c = os.path.join(out_dir, "fc_stubs.c")
    with open(stubs_c, "w") as f:
        f.write("\n".join(stubs) + "\n")
    stubs_o = stubs_c[:-2] + ".o"
    subprocess.run([cc, "-c", "-w", "-fPIC", "-fcommon", stubs_c, "-o", stubs_o], check=True)
    subprocess.run([cc, "-shared", "-o", lib, *objs, stubs_o], check=True)
    return lib
