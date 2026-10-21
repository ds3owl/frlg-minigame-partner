"""One member's own Pokemon Jump state, ported from src/pokemon_jump.c, and the bot that plays it.

A member judges only its own jumps: the vine runs locally from the leader's seed and per-round
timer, and the member reports MONSTATE_HIT itself [UpdateVineHitStates, pokemon_jump.c:2423-2459].
The bot runs the same rules and presses A at the last frame from which the jump clears the vine,
found by simulating ahead on a copy of the state. It never reports a result it did not compute.
"""

import copy

from frlg_minigames import packets

# vine states [pokemon_jump.c:84-96]
(VINE_HIGHEST, VINE_DOWNSWING_HIGHER, VINE_DOWNSWING_HIGH, VINE_DOWNSWING_LOW,
 VINE_DOWNSWING_LOWER, VINE_LOWEST, VINE_UPSWING_LOWER, VINE_UPSWING_LOW, VINE_UPSWING_HIGH,
 VINE_UPSWING_HIGHER, NUM_VINESTATES) = range(11)


def vine_state_timer(state):
    """VINE_STATE_TIMER [pokemon_jump.c:103]"""
    return (state << 8) | 0xFF


JUMP_TYPE_NORMAL, JUMP_TYPE_FAST, JUMP_TYPE_SLOW = 0, 1, 2
JUMP_PEAK = -30
# sJumpOffsets [pokemon_jump.c:2296-2321]; rows are 48 long, zero-filled past the listed values
_P = JUMP_PEAK
JUMP_OFFSETS = {
    JUMP_TYPE_NORMAL: [-3, -6, -8, -10, -13, -15, -17, -19, -21, -23, -25, -27, -28, -29,
                       _P, _P, _P, -28, -27, -26, -25, -23, -22, -20, -18, -17, -15, -13, -11,
                       -8, -6, -4, -1],
    JUMP_TYPE_FAST: [-3, -6, -9, -11, -14, -16, -18, -20, -22, -24, -26, -28, -29, _P, _P,
                     -28, -26, -24, -22, -20, -18, -16, -14, -11, -9, -6, -4, -1],
    JUMP_TYPE_SLOW: [-3, -6, -9, -11, -13, -15, -17, -19, -21, -23, -25, -27, -28, -29,
                     _P, _P, _P, _P, -29, -29, -28, -28, -27, -27, -26, -25, -24, -22, -20,
                     -18, -16, -14, -12, -11, -9, -6, -4, -1],
}
for _row in JUMP_OFFSETS.values():
    _row.extend([0] * (48 - len(_row)))

# sPokeJumpMons [pokemon_jump.c:766-868], all 99 entries: the game's internal species id (Gen 3
# ids differ from the national dex above 251) -> jump type. The leader's InitPlayerAndJumpTypes
# looks every player's species up here [:954-965].
_S, _F, _N = JUMP_TYPE_SLOW, JUMP_TYPE_FAST, JUMP_TYPE_NORMAL
JUMP_TYPES = {
    1: _S, 4: _F, 7: _N, 10: _F, 11: _F, 13: _F, 14: _F, 19: _F, 20: _F, 25: _N, 27: _N,
    29: _N, 32: _N, 35: _N, 37: _N, 39: _S, 43: _S, 46: _F, 52: _N, 54: _S, 56: _F, 58: _F,
    60: _S, 69: _S, 90: _F, 98: _F, 102: _S, 104: _N, 132: _S, 133: _N, 138: _F, 140: _F,
    152: _S, 155: _F, 158: _N, 167: _F, 172: _N, 173: _N, 174: _S, 175: _S, 179: _N, 182: _S,
    183: _S, 191: _S, 194: _S, 204: _S, 209: _N, 213: _S, 216: _N, 218: _S, 220: _N, 228: _F,
    231: _N, 236: _F, 238: _S, 239: _F, 240: _F, 246: _F,
    277: _F, 280: _S, 283: _N, 284: _N, 286: _F, 288: _N, 289: _N, 290: _F, 291: _S, 293: _S,
    295: _S, 298: _F, 392: _N, 393: _N, 311: _S, 306: _S, 301: _F, 370: _N, 350: _S, 315: _N,
    322: _N, 355: _N, 382: _F, 356: _S, 337: _F, 353: _F, 354: _F, 386: _N, 387: _N, 363: _S,
    367: _S, 339: _S, 321: _S, 351: _N, 332: _S, 344: _S, 390: _F, 360: _N, 346: _N, 373: _F,
    395: _F,
}

VINE_BASE_SPEEDS = [26, 31, 36, 41, 46, 51, 56, 61]     # sVineBaseSpeeds [:2116]
VINE_SPEED_DELAYS = [0, 1, 1, 2]                          # sVineSpeedDelays [:2117]
INT_MAX = 0x7FFFFFFF


def _u32(x):
    return x & 0xFFFFFFFF


class JumpModel:
    """The fields of struct PokemonJump one member uses for its own vine and jump."""

    def __init__(self, jump_type=JUMP_TYPE_NORMAL):
        self.jump_type = jump_type
        self.rng_seed = 0
        self.vine_timer = 0
        self.vine_state = VINE_UPSWING_LOWER
        self.prev_vine_state = VINE_UPSWING_LOWER
        self.vine_state_timer = 0
        self.vine_speed = 0
        self.next_vine_speed = 0
        self.vine_speed_stage = 0
        self.vine_base_speed_idx = 0
        self.vine_speed_accel = 0
        self.vine_speed_delay = 0
        self.at_max_speed_stage = False
        self.ignore_jump_input = 0
        self.game_over = False
        self.allow_vine_updates = False
        self.helper_state = 0
        # our player
        self.mon_state = packets.MONSTATE_NORMAL
        self.prev_mon_state = packets.MONSTATE_NORMAL
        self.jump_state = packets.JUMPSTATE_NONE
        self.jump_time_start = 0
        self.jump_offset = 0
        self.jump_offset_idx = INT_MAX
        self._seen_jump_time_start = 0

    # InitVineState [:2028-2036] / ResetVineState [:2038-2052]
    def init_vine_state(self):
        self.vine_timer = 0
        self.vine_state = VINE_UPSWING_LOWER
        self.vine_state_timer = 0
        self.vine_speed = 0
        self.ignore_jump_input = 0
        self.game_over = False

    def reset_vine_state(self):
        self.vine_timer = 0
        self.vine_state_timer = vine_state_timer(VINE_UPSWING_LOWER)
        self.vine_state = VINE_UPSWING_LOW
        self.ignore_jump_input = 0
        self.game_over = False
        self.vine_speed_stage = 0
        self.vine_base_speed_idx = 0
        self.vine_speed_accel = 0
        self.vine_speed_delay = 0
        self.at_max_speed_stage = False
        self.update_vine_speed()

    def reset_for_new_game(self, seed):
        """ResetPlayersForNewGame for our player [pokemon_jump.c:969-981], the new game's seed
        from the leader, and GAME_INTRO's vine reset [:1680-1688]."""
        self.jump_time_start = 0
        self.mon_state = self.prev_mon_state = packets.MONSTATE_NORMAL
        self.jump_offset = 0
        self.jump_offset_idx = INT_MAX
        self.jump_state = packets.JUMPSTATE_NONE
        self.helper_state = 0
        self.rng_seed = seed
        self.allow_vine_updates = True
        self.reset_vine_state()

    def random(self):
        """PokeJumpRandom: ISO_RANDOMIZE1 [:2167-2171; include/random.h:19]"""
        self.rng_seed = _u32(1103515245 * self.rng_seed + 24691)
        return self.rng_seed >> 16

    def get_vine_speed(self):
        """GetVineSpeed [:2071-2087]"""
        if self.game_over:
            return 0
        speed = self.vine_speed
        if self.vine_state_timer <= vine_state_timer(VINE_LOWEST):
            self.vine_speed_accel += 80
            speed += self.vine_speed_accel // 256
        return speed

    def update_vine_speed(self):
        """UpdateVineSpeed [:2119-2165]"""
        self.vine_speed_accel = 0
        if self.vine_speed_delay:
            self.vine_speed_delay -= 1
            if self.at_max_speed_stage:
                if self.random() % 4:
                    self.vine_speed = self.next_vine_speed
                elif self.next_vine_speed > 54:
                    self.vine_speed = 30
                else:
                    self.vine_speed = 82
        else:
            if not (self.vine_base_speed_idx & len(VINE_BASE_SPEEDS)):
                self.next_vine_speed = (VINE_BASE_SPEEDS[self.vine_base_speed_idx]
                                        + self.vine_speed_stage * 7)
                self.vine_speed_delay = VINE_SPEED_DELAYS[self.random() % 4] + 2
                self.vine_base_speed_idx += 1
            else:
                if self.vine_base_speed_idx == len(VINE_BASE_SPEEDS):
                    if self.vine_speed_stage < 3:
                        self.vine_speed_stage += 1
                    else:
                        self.at_max_speed_stage = True
                base = VINE_BASE_SPEEDS[15 - self.vine_base_speed_idx]
                self.next_vine_speed = base + self.vine_speed_stage * 7
                self.vine_base_speed_idx += 1
                if self.vine_base_speed_idx > 15:
                    if self.random() % 4 == 0:
                        self.next_vine_speed -= 5
                    self.vine_base_speed_idx = 0
            self.vine_speed = self.next_vine_speed

    def update_vine_state(self):
        """UpdateVineState [:2054-2069]"""
        if not self.allow_vine_updates:
            return
        self.vine_timer = (self.vine_timer + 1) & 0xFFFF
        self.vine_state_timer += self.get_vine_speed()
        if self.vine_state_timer >= vine_state_timer(NUM_VINESTATES - 1):
            self.vine_state_timer -= vine_state_timer(NUM_VINESTATES - 1)
        self.prev_vine_state = self.vine_state
        self.vine_state = self.vine_state_timer >> 8
        if self.vine_state > VINE_UPSWING_LOWER and self.prev_vine_state < VINE_UPSWING_LOW:
            self.ignore_jump_input += 1
            self.update_vine_speed()

    def reset_vine_after_hit(self):
        """ResetVineAfterHit [:2173-2179]"""
        self.game_over = True
        self.vine_state = VINE_UPSWING_LOWER
        self.vine_state_timer = vine_state_timer(VINE_LOWEST)
        self.allow_vine_updates = True

    def set_mon_state_jump(self):
        self.jump_time_start = self.vine_timer
        self.prev_mon_state = self.mon_state
        self.mon_state = packets.MONSTATE_JUMP

    def set_mon_state_hit(self):
        self.prev_mon_state = self.mon_state
        self.mon_state = packets.MONSTATE_HIT
        self.jump_time_start = self.vine_timer
        self.jump_state = packets.JUMPSTATE_FAILURE

    def set_mon_state_normal(self):
        self.prev_mon_state = self.mon_state
        self.mon_state = packets.MONSTATE_NORMAL

    def handle_swing_round(self, press_a):
        """HandleSwingRound [:1705-1741]; False on the frame a new upswing starts."""
        self.update_vine_state()
        if self.ignore_jump_input:
            self.ignore_jump_input = 0
            return False
        if self.helper_state == 0:
            if self.mon_state == packets.MONSTATE_NORMAL:
                self.helper_state = 1
        if self.helper_state == 1:
            if press_a:
                self.set_mon_state_jump()
                self.helper_state = 2
        elif self.helper_state == 2:
            if self.mon_state == packets.MONSTATE_JUMP:
                self.helper_state = 3
        elif self.helper_state == 3:
            if self.mon_state == packets.MONSTATE_NORMAL:
                self.helper_state = 0
        return True

    def update_vine_hit_states(self, others_hit=False):
        """UpdateVineHitStates [:2423-2459] for our player; others_hit = another player's
        monState is MONSTATE_HIT. False when anyone is hit."""
        if self.vine_state == VINE_UPSWING_LOWER and self.jump_offset == 0:
            if self.prev_mon_state == packets.MONSTATE_JUMP and self.game_over:
                self.jump_state = packets.JUMPSTATE_SUCCESS
            else:
                self.set_mon_state_hit()
        if (self.vine_state == VINE_UPSWING_LOW and self.prev_vine_state == VINE_UPSWING_LOWER
                and self.mon_state != packets.MONSTATE_HIT):
            self.jump_state = packets.JUMPSTATE_SUCCESS
        return not (self.mon_state == packets.MONSTATE_HIT or others_hit)

    def update_own_jump(self):
        """HandleMonState + UpdateJump for our player [:2240-2364]."""
        if self.mon_state == packets.MONSTATE_HIT:
            if self.prev_mon_state != packets.MONSTATE_HIT:
                self.prev_mon_state = packets.MONSTATE_HIT     # [:2280-2286]
            return
        if self.mon_state != packets.MONSTATE_JUMP:
            return
        if self.prev_mon_state != packets.MONSTATE_JUMP or \
                self.jump_time_start != self._seen_jump_time_start:
            self.prev_mon_state = packets.MONSTATE_JUMP
            self.jump_offset_idx = INT_MAX
            self._seen_jump_time_start = self.jump_time_start
        if self.jump_offset_idx != INT_MAX:
            self.jump_offset_idx += 1
            idx = self.jump_offset_idx
        else:
            idx = self.vine_timer - self.jump_time_start
            if idx >= 65000:
                idx -= 65000
                idx += self.vine_timer
            self.jump_offset_idx = idx
        if idx < 4:
            return
        idx -= 4
        offset = JUMP_OFFSETS[self.jump_type][idx] if idx < 48 else 0
        if offset == 0:
            self.set_mon_state_normal()
        self.jump_offset = offset

    def game_round_frame(self, press_a, others_hit=False):
        """GameRound_Member then UpdateGame for one frame [:1378-1388, Task_PokemonJump_Member].
        -> 'continue', 'round_end' or 'hit'."""
        result = "continue"
        if not self.handle_swing_round(press_a):
            result = "round_end"
        elif not self.update_vine_hit_states(others_hit):
            self.reset_vine_after_hit()
            result = "hit"
        self.update_own_jump()
        return result


class JumpBot:
    """Plans one jump per swing by simulating the game's own rules ahead.

    Among the frames from which a jump clears this swing, it takes the earliest one after which
    a clearing jump still exists for the next swing (searched with no pause between swings, the
    worst case); failing that, the earliest clearing frame. The plan is a target vine timer, made
    once per swing when the Pokemon is back on the ground."""

    LOOKAHEAD = 240
    MAX_WAIT = 80

    def __init__(self):
        self._plan = None          # (vine_timer to press at, swing id)

    @staticmethod
    def _run(sim, press_at_step, steps=LOOKAHEAD):
        """-> ('hit'|'round_end'|'continue', frames run) pressing A at step press_at_step."""
        for i in range(steps):
            result = sim.game_round_frame(i == press_at_step)
            if result != "continue":
                return result, i + 1
        return "continue", steps

    @classmethod
    def _clears(cls, model, jump_now):
        sim = copy.copy(model)
        return cls._run(sim, 0 if jump_now else -1)[0] != "hit"

    @classmethod
    def _next_swing_ok(cls, sim):
        """sim stands at a round end; is there any clearing jump in the next swing?"""
        for wait in range(cls.MAX_WAIT):
            probe = copy.copy(sim)
            ok = True
            for i in range(wait):
                if probe.game_round_frame(False) != "continue":
                    ok = False
                    break
            if not ok:
                return False
            if probe.helper_state in (0, 1) and probe.mon_state == packets.MONSTATE_NORMAL \
                    and cls._run(probe, 0)[0] == "round_end":
                return True
        return False

    def _make_plan(self, model):
        first = None
        for wait in range(self.MAX_WAIT):
            probe = copy.copy(model)
            alive = True
            for _ in range(wait):
                if probe.game_round_frame(False) != "continue":
                    alive = False
                    break
            if not alive:
                break
            target = probe.vine_timer + 1      # handle_swing_round increments before reading A
            after = copy.copy(probe)
            result, _ = self._run(after, 0)
            if result != "round_end":
                continue
            if first is None:
                first = target
            if self._next_swing_ok(after):
                return target
        return first

    def should_jump(self, model):
        if model.helper_state not in (0, 1) or model.mon_state != packets.MONSTATE_NORMAL:
            self._plan = None
            return False
        if self._plan is not None and \
                ((self._plan - (model.vine_timer + 1)) & 0xFFFF) > self.LOOKAHEAD + self.MAX_WAIT:
            # A plan from another game (the vine timer restarted) or from before a round sync:
            # it can never come due; plan again from where the vine is now.
            self._plan = None
        if self._plan is None:
            self._plan = self._make_plan(model)
            if self._plan is None:
                return self._clears(model, True)
        if (model.vine_timer + 1) & 0xFFFF == self._plan & 0xFFFF:
            self._plan = None
            return True
        return False

    def reset(self):
        self._plan = None
