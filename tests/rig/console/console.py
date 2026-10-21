"""ctypes drivers for the fake console (build.py): the original game code on one side of the
wire, played by a scripted "Switch player" that only presses buttons."""

import ctypes
import os
import random
import shutil
import tempfile

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames import packets
from frlg_minigames import berrypick_tables as BT
from frlg_minigames.net.frlg.text import charmap

A_BUTTON, B_BUTTON = 0x0001, 0x0002
DPAD_RIGHT, DPAD_LEFT, DPAD_UP, DPAD_DOWN = 0x0010, 0x0020, 0x0040, 0x0080

ROWS = ctypes.c_uint16 * 35
OUT = ctypes.c_uint16 * 7


def _fresh_copy(lib_path):
    """Each console gets its own copy of the library: its globals are the console's RAM."""
    tmp = tempfile.mkdtemp(prefix="fakeconsole-")
    path = os.path.join(tmp, "fc.so")
    shutil.copy(lib_path, path)
    return ctypes.CDLL(path)


def rows_from_positional(rec):
    words = [0] * 35
    for mpid, slot in rec.get("positional", []):
        if 0 <= mpid < 5:
            slot = bytes(slot)[:14].ljust(14, b"\x00")
            for j in range(7):
                words[mpid * 7 + j] = int.from_bytes(slot[2 * j:2 * j + 2], "little")
    return words


class MemberConsole:
    """A member console in a group the PC leads, as a Sim engine (frlg.link.sim)."""

    in_seat_phase = False
    host_in_seat = False
    established = False

    def __init__(self, lib_path, *, activity, name="SWITCH", trainer_id=0x13572468,
                 player=None):
        self.lib = _fresh_copy(lib_path)
        self.lib.fc_link_player_name.restype = ctypes.c_char_p
        self.ni_activity = activity
        self.ni_started = False
        self.lp = linkplayer.LinkPlayer(name=name, trainer_id=trainer_id,
                                        version=linkplayer.VERSION_FIRE_RED)
        encoded = charmap.encode(name, width=8, pad=0xFF)
        self.lib.fc_set_player(ctypes.c_char_p(bytes(encoded)), ctypes.c_uint32(trainer_id))
        self.lib.fc_init_member(activity)
        self.player = player or (lambda console: 0)
        self.last_rows = [0] * 35
        self.frames = 0
        from collections import deque
        self._tables = deque()       # gRfu.recvQueue: one table handled per VBlank

    # Sim engine interface
    def feed_in_frame(self, rec):
        if rec is not None:
            self._tables.append(rows_from_positional(rec))

    def _vblank(self, polled):
        """One console VBlank: RfuMain1_Child dequeues one table and handles it; only when the
        parent's poll left a send credit (childSendCount) does it run the link callback and hand
        gSendCmd out [link_rfu_2.c:965-1008]; otherwise gSendCmd stays put and the game waits on
        IsSendCmdComplete. Then the main loop, every VBlank."""
        if self._tables:
            rows = self._tables.popleft()
            self.last_rows = rows
            self.lib.fc_receive(ROWS(*rows))
        out = OUT()
        if polled:
            self.lib.fc_send(out)
        self.frames += 1
        self.lib.fc_main(self.player(self))
        if self.lib.fc_received_link_players():
            self.established = True
        return list(out)

    def tick(self):
        return self._vblank(True)

    def poll_send_done(self):
        self._vblank(False)      # no send credit this VBlank; the console runs on

    # what tests read
    @property
    def exited(self):
        return bool(ctypes.c_int.in_dll(self.lib, "fc_exited").value)

    @property
    def saves_done(self):
        return ctypes.c_int.in_dll(self.lib, "fc_saves_done").value

    def record_berries_picked(self):
        return self.lib.fc_record_berries_picked()

    def record_jumps_in_row(self):
        return self.lib.fc_record_jumps_in_row()

    def records(self):
        """The console's berryPick records in its save block [TryUpdateRecords :2633-2644]."""
        return {"berries_picked": self.lib.fc_record_berries_picked(),
                "best_score": self.lib.fc_record_berries_best_score(),
                "berries_in_row": self.lib.fc_record_berries_in_row()}

    def link_player_names(self, n):
        return [charmap.decode(self.lib.fc_link_player_name(i)) for i in range(n)]

    def leader_game_state(self):
        words = self.last_rows[0:7]
        if (words[0] & 0xFF00) != 0x2F00:
            return None
        return packets.decode_berry_game_state(packets.bytes_from_words(words[1:7]))


class BerryPlayer:
    """The Switch player: presses a direction when one of its berries is in range (probability
    `skill` per frame, from what its screen shows: the leader's GameState); in prompts presses A
    (YES) or B (NO) every so often. `answers`: play-again answer per game."""

    def __init__(self, num_players, skill, answers, seed=0):
        self.n = num_players
        self.skill = skill
        self.answers = list(answers)
        self.rng = random.Random(seed)
        self.frame = 0
        self.mpid = 1

    def __call__(self, console):
        self.frame += 1
        state = console.leader_game_state()
        if state is not None and state["num_gray_squares"] < 10:
            if state["pick_states"][self.mpid] == 0:
                heads = BT.DODRIO_HEAD_TO_COLUMN_MAP[self.n - 1][self.mpid]
                for head, col in enumerate(heads):
                    if state["fall_dist"][col % 10] in (6, 7) and self.rng.random() < self.skill:
                        return (DPAD_LEFT, DPAD_UP, DPAD_RIGHT)[head]
            return 0
        if console.lib.fc_dodrio_in_prompt():
            if self.frame % 10:
                return 0
            want = self.answers[0] if self.answers else 1
            if console.lib.fc_dodrio_cursor() != want:
                return DPAD_DOWN                      # NONE -> NO, NO <-> YES
            if self.answers:
                self.answers.pop(0)
            return A_BUTTON
        return A_BUTTON if self.frame % 20 == 0 else 0     # advance the result screens


class LeaderConsole:
    """A leader console with one member, as a HostSession activity (frlg.link.host_session)."""

    def __init__(self, lib_path, *, activity, name="SWITCH", trainer_id=0x24681357,
                 species=25, player=None):
        from collections import deque
        self.lib = _fresh_copy(lib_path)
        self.lib.fc_link_player_name.restype = ctypes.c_char_p
        encoded = charmap.encode(name, width=8, pad=0xFF)
        self.lib.fc_set_player(ctypes.c_char_p(bytes(encoded)), ctypes.c_uint32(trainer_id))
        ctypes.c_uint16.in_dll(self.lib, "gFcSpecies").value = species
        self.lib.fc_init_leader(activity)
        self.player = player or (lambda console: 0)
        self._child = deque()
        self._sends = deque()
        self.state = "LINK"
        self.done = False
        self.disconnect_requested = False
        self.close_confirmed = False
        self.frames = 0

    # HostSession activity interface
    @property
    def established(self):
        return bool(self.lib.fc_received_link_players())

    @property
    def trade(self):
        return self

    def feed_child_slot(self, slot):
        slot = bytes(slot)[:14].ljust(14, b"\x00")
        self._child.append([int.from_bytes(slot[2 * j:2 * j + 2], "little") for j in range(7)])

    def _vblank(self):
        """A parent sends every VBlank [RfuMain1_Parent/RfuMain2_Parent]; the emulator's
        transport carries what it cannot send at once later, in order (the queue here)."""
        child = self._child.popleft() if self._child else [0] * 7
        out = OUT()
        self.lib.fc_parent_frame((ctypes.c_uint16 * 7)(*child), out)
        self._sends.append(list(out))
        self.frames += 1
        self.lib.fc_main(self.player(self))
        if self.exited:
            self.close_confirmed = True
            self.disconnect_requested = True

    def tick(self):
        self._vblank()
        return self._sends.popleft()

    def idle_frame(self):
        self._vblank()           # HostSession held this VBlank's row back; the console runs on

    def mark_disconnect_sent(self):
        self.done = True

    # what tests read
    @property
    def exited(self):
        return bool(ctypes.c_int.in_dll(self.lib, "fc_exited").value)

    @property
    def saves_done(self):
        return ctypes.c_int.in_dll(self.lib, "fc_saves_done").value

    def record_jumps_in_row(self):
        return self.lib.fc_record_jumps_in_row()

    def link_player_names(self, n):
        return [charmap.decode(self.lib.fc_link_player_name(i)) for i in range(n)]

    def jump_state(self):
        buf = (ctypes.c_int * 32)()
        self.lib.fc_jump_state(buf)
        return list(buf)

    def set_menu_choice(self, choice):
        ctypes.c_int.in_dll(self.lib, "gFcMenuChoice").value = choice


class JumpLeaderPlayer:
    """The Switch player as the Pokemon Jump leader: watches its own screen (the leader's vine
    and Pokemon, read from the console) and presses A when a jump clears, decided with
    pokejump_model.JumpBot on a copy of that state. Stops jumping after `streak` swings so the
    game ends; presses A through the messages; answers the play-again menu from `answers`
    (0 yes, 1 no)."""

    def __init__(self, streak, answers):
        from frlg_minigames.pokejump_model import JumpBot
        self.streak = streak
        self.answers = list(answers)
        self.bot = JumpBot()
        self.frame = 0
        self.max_streak = 0

    def _model(self, s):
        from frlg_minigames.pokejump_model import JumpModel
        m = JumpModel(s[24])
        (m.vine_state, m.prev_vine_state, m.vine_state_timer, m.vine_speed, m.vine_speed_accel,
         m.vine_timer, m.rng_seed, m.next_vine_speed, m.vine_speed_stage, m.vine_base_speed_idx,
         m.vine_speed_delay) = s[1:12]
        m.rng_seed &= 0xFFFFFFFF
        m.vine_speed_accel &= 0xFFFFFFFF
        m.at_max_speed_stage, m.ignore_jump_input = bool(s[12]), s[13]
        m.game_over, m.allow_vine_updates, m.helper_state = bool(s[14]), bool(s[15]), s[16]
        (m.mon_state, m.prev_mon_state, m.jump_state, m.jump_time_start, m.jump_offset,
         m.jump_offset_idx) = s[18:24]
        m._seen_jump_time_start = m.jump_time_start
        return m

    def __call__(self, console):
        self.frame += 1
        s = console.jump_state()
        if s[0] < 0:
            return A_BUTTON if self.frame % 20 == 0 else 0
        func, active, streak = s[0], s[17], s[25]
        self.max_streak = max(self.max_streak, streak)
        if func == 2 and active:                     # FUNC_GAME_ROUND
            if streak >= self.streak:
                return 0                              # stop jumping: the vine ends the game
            return A_BUTTON if self.bot.should_jump(self._model(s)) else 0
        if func in (0, 1, 2):                         # intro and rounds: A would jump
            return 0
        if func == 4:                                 # FUNC_ASK_PLAY_AGAIN
            console.set_menu_choice(self.answers[0] if self.answers else 1)
        if self.frame % 20 == 0:
            if func == 4 and self.answers:
                self.answers.pop(0)
            return A_BUTTON
        return 0
