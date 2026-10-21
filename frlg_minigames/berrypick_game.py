"""The Dodrio Berry Picking leader's game logic, ported from src/dodrio_berry_picking.c, and the
bots that play the leader's and the virtual members' Dodrio.

The leader decides everything a member sees: berry spawns and falls, who picked what, misses,
the gray squares, and the results block every member copies its record from
[InitResults_Member, dodrio_berry_picking.c:1114-1157; TryUpdateRecords :2633-2644]. So this port
follows the original line by line; only graphics and sound are left out.

Bots leave the Switch player's berries alone: a berry in a column the Switch player reaches is
taken only by a virtual member, only on the last frame a pick can still land, and only if the
Switch player is not attempting it (BerryBot; docs/design.md).
"""

import copy
import random

from frlg_minigames import packets
from frlg_minigames import berrypick_tables as T

MAX_RFU_PLAYERS = 5
NUM_BERRY_COLUMNS = 11
NUM_STATUS_SQUARES = 10            # :129
MAX_FALL_DIST = 10                 # :34
EAT_FALL_DIST = 7                  # :35
NUM_DIFFICULTIES = 7               # :31
MAX_SCORE = 999990                 # :26
MAX_BERRIES = 9999                 # :27
PRIZE_SCORE = 3000                 # :30
PLAYER_NONE = 0xFF                 # :146

PICK_NONE, PICK_RIGHT, PICK_MIDDLE, PICK_LEFT, PICK_DISABLED = range(5)          # :82-88
BERRY_BLUE, BERRY_GREEN, BERRY_GOLD, BERRY_MISSED, BERRY_PRIZE, BERRY_IN_ROW = range(6)
NUM_BERRY_IDS = 6
BERRYSTATE_NONE, BERRYSTATE_PICKED, BERRYSTATE_EATEN, BERRYSTATE_SQUISHED = range(4)
INPUTSTATE_NONE, INPUTSTATE_TRY_PICK, INPUTSTATE_PICKED, INPUTSTATE_ATE_BERRY, \
    INPUTSTATE_BAD_MISS = range(5)
PLAY_AGAIN_NONE, PLAY_AGAIN_YES, PLAY_AGAIN_NO, PLAY_AGAIN_DROPPED = 0, 1, 2, 5
FUNC_PLAY_GAME, FUNC_INIT_RESULTS, FUNC_WAIT_END_GAME = 4, 5, 11     # [:47-60]

BERRY_SCORE_MULTIPLIERS = [10, 30, 50, 50]          # sBerryScoreMultipliers [:2603-2609]
PICK_TO_HEAD = {PICK_LEFT: 0, PICK_MIDDLE: 1, PICK_RIGHT: 2}    # TryPickBerry [:2010-2024]
HEAD_TO_PICK = {0: PICK_LEFT, 1: PICK_MIDDLE, 2: PICK_RIGHT}
PICK_STATE_QUEUE_LEN = 4                             # pickStateQueue[4] [:207]
RESULTS_BLOCK_SIZE = MAX_RFU_PLAYERS * NUM_BERRY_IDS * 2   # u16 berryResults[5][6] [:204]


def get_active_berry_columns(num_players):
    """GetActiveBerryColumns [:2225-2246]"""
    return {1: (4, 7), 2: (3, 8), 3: (2, 9), 4: (1, 10), 5: (0, 11)}[num_players]


class Comm:
    __slots__ = ("pick_state", "ate_berry", "missed_berry")

    def __init__(self):
        self.pick_state = PICK_NONE
        self.ate_berry = False
        self.missed_berry = False


class DodrioLeaderGame:
    """struct DodrioGame as the leader (multiplayer id 0) uses it."""

    def __init__(self, num_players, *, switch_ids=(1,), rng=None):
        self.num_players = num_players
        self.switch_ids = set(switch_ids)      # players a real console controls
        self.rng = rng or random.Random()
        self.berry_col_start, self.berry_col_end = get_active_berry_columns(num_players)
        self.init_game()
        self.set_random_prize()

    # InitDodrioGame [:752-806]
    def init_game(self):
        n = self.num_players
        self.num_gray_squares = 0
        self.berries_falling = False
        self.all_ready_to_end = False
        self.pick_state_queue = [PICK_NONE] * PICK_STATE_QUEUE_LEN
        self.input_state = [INPUTSTATE_NONE] * MAX_RFU_PLAYERS
        self.input_delay = [0] * MAX_RFU_PLAYERS
        prize = getattr(self, "berry_results", None)
        self.berry_results = [[0] * NUM_BERRY_IDS for _ in range(MAX_RFU_PLAYERS)]
        if prize is not None:
            for i in range(MAX_RFU_PLAYERS):
                self.berry_results[i][BERRY_PRIZE] = prize[i][BERRY_PRIZE]
        self.ready_to_end = [False] * MAX_RFU_PLAYERS
        self.fall_timer = [0] * NUM_BERRY_COLUMNS
        self.new_berry_timer = [0] * NUM_BERRY_COLUMNS
        self.eat_timer = [0] * NUM_BERRY_COLUMNS
        self.berry_state = [BERRYSTATE_NONE] * NUM_BERRY_COLUMNS
        self.players_attempting_pick = [[PLAYER_NONE, PLAYER_NONE] for _ in range(NUM_BERRY_COLUMNS)]
        self.berry_eaten_by = [0] * NUM_BERRY_COLUMNS
        self.prev_berry_ids = [0] * NUM_BERRY_COLUMNS
        self.difficulty = [0] * MAX_RFU_PLAYERS
        self.berries_eaten = [0] * MAX_RFU_PLAYERS
        self.berries_picked_in_row = 0
        self.max_berries_picked_in_row = 0
        self.comm = [Comm() for _ in range(MAX_RFU_PLAYERS)]
        # sGame->player.berries (the leader's live copy) and players[0].berries (its loopback)
        self.fall_dist = [0] * NUM_BERRY_COLUMNS
        self.ids = [0] * NUM_BERRY_COLUMNS
        self.loop_ids = [0] * NUM_BERRY_COLUMNS
        self.func = FUNC_PLAY_GAME
        self.frames = 0
        assert n == self.num_players

    def set_random_prize(self):
        """SetRandomPrize [:2610-2623]: one Random() % 10 picks the prize from the player
        count's row of sPrizeBerryIds, for everyone."""
        prize_set = {4: 1, 5: 2}.get(self.num_players, 0)
        idx = self.rng.getrandbits(16) % 10
        for i in range(MAX_RFU_PLAYERS):
            self.berry_results[i][BERRY_PRIZE] = T.PRIZE_BERRY_IDS[prize_set][idx]

    def init_first_wave(self):
        """InitFirstWaveOfBerries [:1871-1883]"""
        for i in range(self.berry_col_start, self.berry_col_end):
            self.fall_dist[i] = 1 if i % 2 == 0 else 0
            self.ids[i] = BERRY_BLUE

    # --- per-frame -----------------------------------------------------------------------------

    def _loopback(self, sent):
        """RecvPacket_GameState(0, ...) on the leader reads its own previous GameState back
        [RecvLinkData_Gameplay :1491-1503; comm.c:156-227]."""
        if sent is None:
            return
        state = packets.decode_berry_game_state(sent)
        self.loop_ids = state["berry_ids"] + [state["berry_ids"][0]]
        for i in range(MAX_RFU_PLAYERS):
            self.comm[i].pick_state = state["pick_states"][i]
            self.comm[i].ate_berry = state["ate_berry"][i]
            self.comm[i].missed_berry = state["missed_berry"][i]
        self.num_gray_squares = state["num_gray_squares"]
        self.berries_falling = state["berries_falling"]
        self.all_ready_to_end = state["all_ready_to_end"]

    def recv_gameplay(self, sent, member_picks, row0_is_packet=True):
        """RecvLinkData_Gameplay [:1491-1560]. member_picks: {id: pickState} from this frame's
        PICK_STATE packets. RecvPacket_PickState ignores every row unless row 0 holds a
        SEND_PACKET [comm.c:237-252]. The C's out-of-bounds write players[PLAYER_NONE] [:1993]
        lands outside sGame and is not modelled."""
        if row0_is_packet:
            self._loopback(sent)
        for i in range(1, self.num_players):
            if self.input_state[i] == INPUTSTATE_NONE:
                if row0_is_packet and i in member_picks:
                    self.comm[i].pick_state = member_picks[i]
                else:
                    self.comm[i].pick_state = PICK_NONE
        for i in range(self.num_players):
            c = self.comm[i]
            if c.pick_state != PICK_NONE and self.input_state[i] == INPUTSTATE_NONE:
                self.input_state[i] = INPUTSTATE_TRY_PICK
            st = self.input_state[i]
            if st in (INPUTSTATE_TRY_PICK, INPUTSTATE_PICKED, INPUTSTATE_ATE_BERRY):
                self.input_delay[i] += 1
                if self.input_delay[i] >= 6:
                    self._clear_input(i)
            elif st == INPUTSTATE_BAD_MISS:
                self.input_delay[i] += 1
                if self.input_delay[i] >= 40:
                    self._clear_input(i)

    def _clear_input(self, i):
        self.input_delay[i] = 0
        self.input_state[i] = INPUTSTATE_NONE
        self.comm[i].pick_state = PICK_NONE
        self.comm[i].ate_berry = False
        self.comm[i].missed_berry = False

    def update_pick_state_queue(self, pick):
        """UpdatePickStateQueue [:2648-2659]"""
        nxt = self.pick_state_queue[-1]
        self.pick_state_queue = [pick] + self.pick_state_queue[:-1]
        return nxt

    def play_frame_leader_input(self, leader_pick):
        """PlayGame_Leader's input part [:968-1006]; leader_pick is the leader bot's press this
        frame (PICK_NONE for none). -> False once the game is over (WAIT_END_GAME)."""
        if self.num_gray_squares >= NUM_STATUS_SQUARES:
            return False
        if self.input_state[0] == INPUTSTATE_NONE:
            if leader_pick != PICK_NONE:
                if self.comm[0].pick_state == PICK_NONE:
                    self.comm[0].ate_berry = False
                    self.comm[0].pick_state = self.update_pick_state_queue(leader_pick)
            else:
                self.comm[0].pick_state = self.update_pick_state_queue(PICK_NONE)
        return True

    def player_id_at_column(self, column):
        return T.PLAYER_ID_AT_COLUMN[self.num_players - 1][column]

    def _delay_stage(self, player_id):
        if player_id >= MAX_RFU_PLAYERS:
            # GetPlayerIdAtColumn's 'x' (9) reads past difficulty[]; never on an active column.
            return 0
        stage = self.difficulty[player_id] // NUM_DIFFICULTIES
        return min(stage, len(T.BERRY_FALL_DELAYS) - 1)

    def update_falling_berries(self):
        """UpdateFallingBerries [:2058-2140]"""
        other_missed = False
        self.berries_falling = False
        for i in range(self.berry_col_start, self.berry_col_end - 1):
            if self.berry_state[i] in (BERRYSTATE_NONE, BERRYSTATE_PICKED):
                self.berries_falling = True
                if self.fall_dist[i] >= MAX_FALL_DIST:
                    self.fall_dist[i] = MAX_FALL_DIST
                    self.berry_state[i] = BERRYSTATE_SQUISHED
                    if self.num_gray_squares < NUM_STATUS_SQUARES or other_missed:
                        other_missed = True
                        if self.num_gray_squares < NUM_STATUS_SQUARES:
                            self.num_gray_squares += 1
                        self.increment_berry_result(BERRY_MISSED, i, 0)
                        self.update_berries_picked_in_row(False)
                else:
                    delay = T.BERRY_FALL_DELAYS[self._delay_stage(self.player_id_at_column(i))][
                        self.ids[i]]
                    self.fall_timer[i] += 1
                    if self.fall_timer[i] >= delay:
                        self.fall_dist[i] += 1
                        self.fall_timer[i] = 0
                    self.handle_pick_berries()
            elif self.berry_state[i] == BERRYSTATE_EATEN:
                self.new_berry_timer[i] += 1
                if self.new_berry_timer[i] >= 20:
                    self.comm[self.berry_eaten_by[i]].ate_berry = False
                    self.new_berry_timer[i] = 0
                    self.fall_timer[i] = 0
                    self.berry_state[i] = BERRYSTATE_NONE
                    self.fall_dist[i] = 1
                    self.ids[i] = self.get_new_berry_id(self.player_id_at_column(i), i)
            elif self.berry_state[i] == BERRYSTATE_SQUISHED:
                self.new_berry_timer[i] += 1
                if self.new_berry_timer[i] >= 20 and self.num_gray_squares < NUM_STATUS_SQUARES:
                    self.new_berry_timer[i] = 0
                    self.fall_timer[i] = 0
                    self.berry_state[i] = BERRYSTATE_NONE
                    self.fall_dist[i] = 1
                    self.prev_berry_ids[i] = self.ids[i]
                    self.ids[i] = self.get_new_berry_id(self.player_id_at_column(i), i)

    def try_pick_berry(self, player_id, pick_state, column):
        """TryPickBerry [:2004-2056]"""
        pick = PICK_TO_HEAD.get(pick_state, 0)
        target = T.DODRIO_HEAD_TO_COLUMN_MAP[self.num_players - 1][player_id][pick]
        if self.fall_dist[column] in (EAT_FALL_DIST - 1, EAT_FALL_DIST):
            if column == target:
                if self.berry_state[column] in (BERRYSTATE_PICKED, BERRYSTATE_EATEN):
                    self.comm[player_id].missed_berry = True
                    return False
                return True
        elif column == target:
            self.input_state[player_id] = INPUTSTATE_BAD_MISS
            self.comm[player_id].missed_berry = True
        return False

    def handle_pick_berries(self):
        """HandlePickBerries [:1888-2002]"""
        if self.num_gray_squares >= NUM_STATUS_SQUARES:
            return
        start, end = self.berry_col_start, self.berry_col_end
        for i in range(self.num_players):
            pick = self.comm[i].pick_state
            if pick != PICK_NONE and self.input_state[i] == INPUTSTATE_TRY_PICK:
                for j in range(start, end):
                    column = T.ACTIVE_COLUMN_MAP[0][0][j]
                    if i in self.players_attempting_pick[column]:
                        break
                    if self.try_pick_berry(i, pick, column):
                        for k in range(2):
                            if self.players_attempting_pick[column][k] == PLAYER_NONE:
                                self.players_attempting_pick[column][k] = i
                                self.input_state[i] = INPUTSTATE_PICKED
                                self.berry_state[column] = BERRYSTATE_PICKED
                                break
                        break
                    if self.comm[i].missed_berry:
                        break
        for j in range(start, end):
            missed_id = PLAYER_NONE
            column = T.ACTIVE_COLUMN_MAP[0][0][j]
            if self.berry_state[column] != BERRYSTATE_PICKED:
                continue
            stage = self._delay_stage(self.player_id_at_column(column))
            remaining = _fall_delay_flat(stage, self.loop_ids[column]) - self.fall_timer[column]
            if remaining < 6:
                self.eat_timer[column] = (self.eat_timer[column] + remaining) & 0xFF   # u8 eatTimer
            self.eat_timer[column] = (self.eat_timer[column] + 1) & 0xFF
            if self.eat_timer[column] < 6:
                continue
            self.eat_timer[column] = 0
            first, second = self.players_attempting_pick[column]
            if first == PLAYER_NONE and second == PLAYER_NONE:
                continue
            if first != PLAYER_NONE and second == PLAYER_NONE:
                picked = first
            elif not (self.rng.getrandbits(16) & 1):
                picked, missed_id = first, second
            else:
                picked, missed_id = second, first
            self.fall_dist[column] = EAT_FALL_DIST
            self.berry_state[column] = BERRYSTATE_EATEN
            self.input_state[picked] = INPUTSTATE_ATE_BERRY
            self.berry_eaten_by[column] = picked
            self.comm[picked].ate_berry = True
            if missed_id != PLAYER_NONE:
                self.comm[missed_id].missed_berry = True
            self.berries_eaten[picked] = (self.berries_eaten[picked] + 1) & 0xFFFF
            self.increment_berry_result(BERRY_BLUE, column, picked)
            self.update_berries_picked_in_row(True)
            self.try_increment_difficulty(picked)
            self.prev_berry_ids[column] = self.ids[column]
            self.ids[column] = BERRY_MISSED
            self.players_attempting_pick[column] = [PLAYER_NONE, PLAYER_NONE]

    def increment_berry_result(self, berry_arg, column, player_id):
        """IncrementBerryResult [:2405-2540]: a pick counts players[0].berries.ids[column] (the
        loopback copy); a squish charges every player whose head reaches the column."""
        if berry_arg != BERRY_MISSED:
            # No filter: a loopback id of 3 counts into BERRY_MISSED, as the C does [:2414-2417].
            berry = self.loop_ids[column]
            self.berry_results[player_id][berry] = min(
                self.berry_results[player_id][berry] + 1, 20000)
            return
        for p in MISS_CHARGES.get(self.num_players, {}).get(column, ()):
            self.berry_results[p][BERRY_MISSED] += 1

    def update_berries_picked_in_row(self, picked):
        """UpdateBerriesPickedInRow [:2542-2563]: five-player games only."""
        if self.num_players != MAX_RFU_PLAYERS:
            return
        if picked:
            self.berries_picked_in_row += 1
            if self.berries_picked_in_row > self.max_berries_picked_in_row:
                self.max_berries_picked_in_row = self.berries_picked_in_row
            if self.berries_picked_in_row > MAX_BERRIES:
                self.berries_picked_in_row = MAX_BERRIES
        else:
            if self.berries_picked_in_row > self.max_berries_picked_in_row:
                self.max_berries_picked_in_row = self.berries_picked_in_row
            self.berries_picked_in_row = 0

    def set_max_berries_picked_in_row(self):
        for i in range(self.num_players):
            self.berry_results[i][BERRY_IN_ROW] = self.max_berries_picked_in_row

    def try_increment_difficulty(self, player_id):
        """TryIncrementDifficulty [:2282-2287]"""
        d = self.difficulty[player_id]
        threshold = (T.DIFFICULTY_THRESHOLDS[d % NUM_DIFFICULTIES] + (d // NUM_DIFFICULTIES) * 100) & 0xFF
        if self.berries_eaten[player_id] >= threshold:
            self.difficulty[player_id] = (d + 1) & 0xFF       # u8 difficulty[] [:207]

    def get_new_berry_id(self, player_id, column):
        """GetNewBerryId [:2295-2321]"""
        n = self.num_players - 1
        if player_id >= MAX_RFU_PLAYERS:
            return BERRY_BLUE
        left, middle, right = T.DODRIO_NEIGHBOR_MAP[n][player_id]
        for c in T.UNSHARED_COLUMNS[n]:
            if c == 0:
                break
            if column == c:
                return self._berry_id_by_difficulty(self.difficulty[middle], column)
        highest = max(self.difficulty[left], self.difficulty[middle])
        highest = max(highest, self.difficulty[right])
        return self._berry_id_by_difficulty(highest, column)

    def _berry_id_by_difficulty(self, difficulty, column):
        """GetNewBerryIdByDifficulty [:2325-2360]"""
        prev = self.prev_berry_ids[column]
        d = difficulty % NUM_DIFFICULTIES
        if d == 0:
            return BERRY_BLUE
        if d == 1:
            return BERRY_GREEN
        if d == 2:
            return BERRY_GOLD
        if d == 3:
            return BERRY_GREEN if prev == BERRY_BLUE else BERRY_BLUE
        if d == 4:
            return BERRY_GOLD if prev == BERRY_BLUE else BERRY_BLUE
        if d == 5:
            return BERRY_GREEN if prev == BERRY_GOLD else BERRY_GOLD
        if prev == BERRY_BLUE:
            return BERRY_GREEN
        if prev == BERRY_GREEN:
            return BERRY_GOLD
        return BERRY_BLUE

    def leader_frame(self, sent, member_picks=None, leader_pick=PICK_NONE,
                     row0_is_packet=True, ready_ids=()):
        """One Task_DodrioGame_Leader VBlank in PLAY_GAME or WAIT_END_GAME [:888-895]:
        Recv, the function, then the GameState it sends (None when it sends none). `sent` is the
        packet in row 0, the leader's own previous gSendCmd, read only when row0_is_packet
        [comm.c:169-170]; `member_picks` {id: pickState} this frame's PICK_STATE rows,
        `ready_ids` the members whose row carries READY_END. self.func moves to WAIT_END_GAME and
        INIT_RESULTS as the original's does."""
        self.frames += 1
        if self.func == FUNC_PLAY_GAME:
            self.recv_gameplay(sent, member_picks or {}, row0_is_packet)
            if not self.play_frame_leader_input(leader_pick):
                self.func = FUNC_WAIT_END_GAME        # PlayGame_Leader [:1007-1009]
            self.update_falling_berries()
        elif self.func == FUNC_WAIT_END_GAME:
            if row0_is_packet:
                self._loopback(sent)                   # RecvLinkData_ReadyToEnd [:1563-1607]
            if row0_is_packet:
                for i in ready_ids:
                    if 1 <= i < self.num_players:
                        self.ready_to_end[i] = True
            self.update_falling_berries()              # WaitEndGame_Leader [:1047-1066]
            if self.ready_to_end_leader():
                self.set_max_berries_picked_in_row()
                self.func = FUNC_INIT_RESULTS
            else:
                self.update_all_ready_to_end()
        if self.func not in (FUNC_PLAY_GAME, FUNC_WAIT_END_GAME):
            return None               # SendLinkData_Leader sends nothing in INIT_RESULTS [:1629]
        return self.game_state_packet()

    # --- end of game ---------------------------------------------------------------------------

    def ready_to_end_leader(self):
        """ReadyToEndGame_Leader [:2255-2264]"""
        if self.num_gray_squares >= NUM_STATUS_SQUARES and not self.berries_falling:
            self.num_gray_squares = NUM_STATUS_SQUARES
            return bool(self.all_ready_to_end)
        return False

    def update_all_ready_to_end(self):
        """WaitEndGame_Leader [:1047-1066]"""
        self.all_ready_to_end = all(self.ready_to_end[i] for i in range(1, self.num_players))

    def score(self, player_id):
        """GetScore [:2722-2738]"""
        r = self.berry_results[player_id]
        got = sum(r[i] * BERRY_SCORE_MULTIPLIERS[i] for i in range(BERRY_MISSED))
        lost = r[BERRY_MISSED] * BERRY_SCORE_MULTIPLIERS[BERRY_MISSED]
        return 0 if got <= lost else got - lost

    def highest_score(self):
        return min(max(self.score(i) for i in range(self.num_players)), MAX_SCORE)

    def berries_picked(self, player_id):
        """GetBerriesPicked [:2625-2631]"""
        r = self.berry_results[player_id]
        return min(r[BERRY_BLUE] + r[BERRY_GREEN] + r[BERRY_GOLD], MAX_BERRIES)

    def results_block(self):
        """u16 berryResults[5][6] as SendBlock sends it [InitResults_Leader :1076-1080]."""
        out = bytearray()
        for row in self.berry_results:
            for v in row:
                out += (v & 0xFFFF).to_bytes(2, "little")
        return bytes(out)

    def game_state_packet(self):
        """SendPacket_GameState from the leader's live berries [:1629-1656; comm.c:96-154]."""
        return packets.encode_berry_game_state(
            fall_dist=self.fall_dist[:10], berry_ids=self.ids[:10],
            pick_states=[c.pick_state for c in self.comm],
            ate_berry=[c.ate_berry for c in self.comm],
            missed_berry=[c.missed_berry for c in self.comm],
            num_gray_squares=self.num_gray_squares, berries_falling=self.berries_falling,
            all_ready_to_end=self.all_ready_to_end)


def _fall_delay_flat(stage, berry_id):
    """sBerryFallDelays[stage][id] read as the C does: an id of 3 runs into the next row
    [HandlePickBerries :1944]; past the table the padding before sTreeBorderXPos is zero."""
    flat = [v for row in T.BERRY_FALL_DELAYS for v in row]
    index = stage * 3 + berry_id
    return flat[index] if index < len(flat) else 0


# IncrementBerryResult's BERRY_MISSED switch [:2420-2535]: players charged per squished column.
MISS_CHARGES = {
    5: {0: (2, 3), 1: (3,), 2: (3, 4), 3: (4,), 4: (4, 0), 5: (0,), 6: (0, 1), 7: (1,),
        8: (1, 2), 9: (2,)},
    4: {1: (2, 3), 2: (3,), 3: (3, 0), 4: (0,), 5: (0, 1), 6: (1,), 7: (1, 2), 8: (2,)},
    3: {2: (1, 2), 3: (2,), 4: (2, 0), 5: (0,), 6: (0, 1), 7: (1,)},
    2: {3: (0, 1), 4: (0,), 5: (0, 1), 6: (1,)},
}


class BerryBot:
    """Chooses a bot player's pick for one frame, by the leader's own rules.

    A bot reaches three columns (left, middle, right head). It picks a berry that will still be in
    range (fallDist 6 or 7 [TryPickBerry]) when its pick lands - the leader's own input goes
    through a four-frame queue [UpdatePickStateQueue :2648] - and is not already picked. A column it
    shares with the Switch player is never taken by the leader's Dodrio, and by a virtual member
    only on the last frame a pick can land and only if the Switch player is not attempting it, so
    the Switch player's count is their own picks."""

    MARGIN = 2
    SCAN_FRAMES = 12        # look-ahead window for the last-chance test

    def __init__(self, game, player_id, lag=0):
        self.game = game
        self.player_id = player_id
        self.lag = lag

    def _columns(self):
        g = self.game
        return T.DODRIO_HEAD_TO_COLUMN_MAP[g.num_players - 1][self.player_id]

    def _shared_with_switch(self, column):
        g = self.game
        for other in g.switch_ids:
            if column in T.DODRIO_HEAD_TO_COLUMN_MAP[g.num_players - 1][other]:
                return True
        return False

    def _frames_in_range(self, column):
        """Frames until the berry leaves pick range (fallDist 6..7)."""
        g = self.game
        delay = T.BERRY_FALL_DELAYS[g._delay_stage(g.player_id_at_column(column))][g.ids[column]]
        left = delay - g.fall_timer[column]
        if g.fall_dist[column] == EAT_FALL_DIST - 1:
            return left + delay
        if g.fall_dist[column] == EAT_FALL_DIST:
            return left
        return 0

    @staticmethod
    def _attempting(game, player, column):
        return player in game.players_attempting_pick[column] or \
            (game.berry_state[column] == BERRYSTATE_EATEN and game.berry_eaten_by[column] == player)

    def _press_lands(self, player, column, wait):
        """On a copy: `wait` quiet frames, then `player` presses the head over `column`; does
        the leader's HandlePickBerries register the attempt that frame?"""
        g = copy.deepcopy(self.game)
        heads = T.DODRIO_HEAD_TO_COLUMN_MAP[g.num_players - 1][player]
        pick = HEAD_TO_PICK[heads.index(column)]
        for _ in range(wait):
            g.leader_frame(None, {}, PICK_NONE)
        if g.func != FUNC_PLAY_GAME or g.input_state[player] != INPUTSTATE_NONE:
            return False
        g.leader_frame(None, {player: pick} if player else {}, pick if player == 0 else PICK_NONE)
        return self._attempting(g, player, column)

    def _switch_could_land_later(self, column):
        g = self.game
        for s in g.switch_ids:
            if column not in T.DODRIO_HEAD_TO_COLUMN_MAP[g.num_players - 1][s]:
                continue
            for wait in range(1, self.SCAN_FRAMES + 1):
                if self._press_lands(s, column, wait):
                    return True
        return False

    def _lands_now(self, head, column):
        return self._press_lands(self.player_id, column, 0)

    def choose(self):
        g = self.game
        if g.input_state[self.player_id] != INPUTSTATE_NONE or \
                g.comm[self.player_id].pick_state != PICK_NONE:
            return PICK_NONE
        if self.player_id == 0 and any(q != PICK_NONE for q in g.pick_state_queue):
            return PICK_NONE        # a press is still in the leader's input queue
        best = None
        for head, column in enumerate(self._columns()):
            if not g.berry_col_start <= column < g.berry_col_end - 1:
                continue
            if g.berry_state[column] != BERRYSTATE_NONE or g.ids[column] >= BERRY_MISSED:
                continue
            left = self._frames_in_range(column)
            if self._shared_with_switch(column):
                # The leader's own presses are four frames late, so its Dodrio never takes a
                # berry the Switch player can reach. A virtual member's press lands this very
                # frame: it takes such a berry only when a Switch press next frame could no
                # longer land on it (found by running the leader's own rules ahead on a copy),
                # its own press now does, and the Switch player is not attempting it.
                switch_trying = any(g.input_state[s] in (INPUTSTATE_TRY_PICK, INPUTSTATE_PICKED)
                                    for s in g.switch_ids)
                if self.player_id == 0 or switch_trying or left > 2 * self.SCAN_FRAMES:
                    continue
                if self._switch_could_land_later(column) or not self._lands_now(head, column):
                    continue
            elif left <= self.lag + self.MARGIN:
                continue
            if best is None or left < best[0]:
                best = (left, head)
        return PICK_NONE if best is None else HEAD_TO_PICK[best[1]]
