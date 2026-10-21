"""Pokemon Jump as one member (Task_PokemonJump_Member), in two layers.

JumpMemberGame is the member's game: the function hand-shake with the leader, its own vine and jump
(pokejump_model), the bot's A presses and the MemberState packet it sends. It knows nothing about
the radio, so it also serves as a member that exists only inside the PC leader's command table
(pokejump_leader.VirtualJumpMember).

PokeJumpMemberEngine adds the wireless child's plumbing and plugs into frlg.link.sim.Sim exactly as
the link layer's trade engine does: feed_in_frame() gets the parent's table each VBlank, tick() returns this
member's seven-word gSendCmd. The Switch leads; the PC is the member.

The member console's path, in order:
  Task_PlayerExchange: SEND_PLAYER_IDS, SEND_BLOCK_REQ -> our 200-byte LinkPlayer send
      [link_rfu_2.c:1813-1899]
  Task_RunScriptAndFadeToActivity: one member-initiated standby [union_room.c:1975-2010;
      Rfu_LinkStandby, link_rfu_2.c:1564-1590]
  Task_CommunicateMonInfo: MonInfo packet every frame until every player's arrived
      [pokemon_jump.c:1982-2013], 20 frames, then Task_PokemonJump_Member [:1023-1083]
  Task_PokemonJump_Member: follow the leader's funcId; report funcFinished for it
      [:1188-1279]
"""

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames import packets
from frlg_minigames.pokejump_model import JUMP_TYPES, JumpBot, JumpModel
from frlg_minigames.net.gba import barrier as barriermod, block, rfu

ACTIVITY_POKEMON_JUMP = 9
CHILD_SLOT = 0     # gRfu.childSlot; ids[childSlot] is our multiplayer id [link_rfu_2.c:1058-1066]

# enum at pokemon_jump.c:57-68
FUNC_GAME_INTRO, FUNC_WAIT_ROUND, FUNC_GAME_ROUND, FUNC_GAME_OVER = 0, 1, 2, 3
FUNC_ASK_PLAY_AGAIN, FUNC_RESET_GAME, FUNC_EXIT, FUNC_GIVE_PRIZE, FUNC_SAVE, FUNC_NONE = range(4, 10)
FUNC_NAMES = {0: "GAME_INTRO", 1: "WAIT_ROUND", 2: "GAME_ROUND", 3: "GAME_OVER",
              4: "ASK_PLAY_AGAIN", 5: "RESET_GAME", 6: "EXIT", 7: "GIVE_PRIZE", 8: "SAVE",
              9: "NONE"}

# SetLinkTimeInterval(LINK_INTERVAL_SHORT): a packet every 4th frame [pokemon_jump.c:142-145, 1084]
SHORT_INTERVAL_MASK = 3

SPECIES_PIKACHU = 25      # in sPokeJumpMons [pokemon_jump.c:777]
SPECIES_CHARMANDER = 4    # JUMP_TYPE_FAST: the shortest air time [pokemon_jump.c:769]

# Pre-game frames after the Pokemon info is complete [Task_StartPokemonJump cases 1-5]: graphics
# load, fade, then a 20-frame delay before the member task runs. The bot only needs to stay quiet.
POST_MON_INFO_FRAMES = 20

# Frames the bot spends in functions whose length a console decides locally (animations,
# messages). The leader waits for every member's funcFinished [RecvLinkData_Leader, :1114-1137], so
# these only need to be long enough to look like a console and short enough not to stall the group.
INTRO_FRAMES = 150            # DoGameIntro: names 120f + countdown [:1638-1690]
HIT_EFFECT_FRAMES = 130       # DoVineHitEffect after everyone jumped or was hit [:1743-1795]
PLAY_AGAIN_FRAMES = 30        # DoPlayAgainPrompt: the bot answers like a quick player
PRIZE_FRAMES = 200            # TryGivePrize messages [:1797-1830]
RESET_FRAMES = 10             # CloseMessageAndResetScore
EXIT_FRAMES = 150             # ClosePokeJumpLink before SetCloseLinkCallback [:1909-1950]
SAVE_STANDBY_ROUNDS = 3       # Task_LinkFullSave [save.c:882-962]
# A member re-sends READY_EXIT_STANDBY every 60 frames until every player is ready; there is no
# give-up timer [SendReadyExitStandbyUntilAllReady, link_rfu_2.c:1522-1548].
STANDBY_RESEND_FRAMES = 60

PLAY_AGAIN_NO, PLAY_AGAIN_YES = 1, 2      # pokemon_jump.c:116-117

ENTRY = "ENTRY"
STANDBY = "STANDBY"
MON_INFO = "MON_INFO"
PLAYING = "PLAYING"


class JumpMemberGame:
    """Task_PokemonJump_Member without the radio: the function hand-shake, this member's vine and
    jump, the bot, and the MemberState packet [pokemon_jump.c:1188-1279]."""

    def __init__(self, *, species=SPECIES_CHARMANDER, personality=0, ot_id=0, play_again=True,
                 log=lambda *a: None):
        self.species = species
        self.personality = personality
        self.ot_id = ot_id
        self.log = log
        self.info = getattr(log, "info", log)
        self.mpid = None
        self.player_count = None

        self.func_id = FUNC_GAME_INTRO
        self.func_active = False
        self.func_finished = False
        self._func_frames = 0
        self.funcs_seen = []
        self.leader_state = None
        self.link_timer = 0
        self.link_timer_limit = None      # None = LINK_INTERVAL_NONE (no packets)
        self.play_again_state = 0
        if species not in JUMP_TYPES:
            raise ValueError(f"species {species} cannot enter Pokemon Jump")
        self.model = JumpModel(JUMP_TYPES[species])
        self.bot = JumpBot()
        self.play_again = play_again
        self.others = {}          # mpid -> {"mon_state", "jump_state"} from their packets
        self.rounds_cleared = 0
        self.games = 0
        self.hits = 0
        self._save_rounds_left = 0
        self._sub = 0

    @property
    def reached_round(self):
        """Stage-1 gate passed: the leader moved the group past the intro."""
        return any(f in (FUNC_WAIT_ROUND, FUNC_GAME_ROUND) for f in self.funcs_seen)

    def mon_info_packet(self):
        """SendPacket_MonInfo [pokemon_jump.c:2715-2723]."""
        return packets.encode_jump_mon_info(self.species, self.personality, self.ot_id)

    def start_game(self):
        """Task_StartPokemonJump case 5 then InitGame: the vine's initial state, both sides in
        RESET_GAME with linkTimerLimit 0 (a packet every frame) [pokemon_jump.c:903-911,
        914-945, 1068-1080]."""
        self.model.init_vine_state()
        self._set_func(FUNC_RESET_GAME)
        self.link_timer_limit = 0
        self.link_timer = 0

    # --- what the leader's packets tell us -----------------------------------------------------

    def observe_leader(self, state, others=None):
        """RecvLinkData_Member [pokemon_jump.c:1197-1237]: the leader's LeaderState and the other
        members' MemberStates. Move on only once our function finished."""
        if others:
            for mpid, st in others.items():
                self.others[mpid] = {"mon_state": st["mon_state"], "jump_state": st["jump_state"]}
        if state is None:
            return
        self.leader_state = state
        self.others[0] = {"mon_state": state["mon_state"], "jump_state": state["jump_state"]}
        if self.func_finished and state["func_id"] != self.func_id:
            self._set_func(state["func_id"])

    # --- member functions ----------------------------------------------------------------------

    def _set_func(self, func_id):
        """SetFunc_Member [pokemon_jump.c:1188-1195]; each function's first state runs here."""
        self.func_id = func_id
        self.func_active = True
        self.func_finished = False
        self._func_frames = 0
        self._sub = 0
        self.model.helper_state = 0      # SetFunc_Member: mainState = helperState = 0 [:1188-1195]
        self.funcs_seen.append(func_id)
        self.info(f"[jump] Leader moved to {FUNC_NAMES.get(func_id, func_id)}.")
        data = self.leader_state["data"] if self.leader_state else 0
        m = self.model
        if func_id == FUNC_GAME_INTRO:
            self.link_timer_limit = None                  # [:1302-1314]
            m.rng_seed = data
        elif func_id == FUNC_WAIT_ROUND:
            m.jump_state = packets.JUMPSTATE_NONE         # ResetPlayersJumpStates
            for o in self.others.values():
                o["jump_state"] = packets.JUMPSTATE_NONE
            self.link_timer_limit = None
            m.vine_timer = data                           # [:1338-1352]
        elif func_id in (FUNC_ASK_PLAY_AGAIN, FUNC_GIVE_PRIZE, FUNC_EXIT):
            self.link_timer_limit = None
        elif func_id == FUNC_SAVE:
            self._save_rounds_left = SAVE_STANDBY_ROUNDS
            self.link_timer_limit = None                  # SavePokeJump case 1 [:1612-1616]

    def _short_interval(self):
        """SetLinkTimeInterval(LINK_INTERVAL_SHORT): send now, then every 4th frame."""
        self.link_timer_limit = SHORT_INTERVAL_MASK
        self.link_timer = 0

    def _others_hit(self):
        return any(o.get("mon_state") == packets.MONSTATE_HIT for o in self.others.values())

    def _all_jumped_or_hit(self):
        """AllPlayersJumpedOrHit [:2461-2474]"""
        if self.model.jump_state == packets.JUMPSTATE_NONE:
            return False
        need = [i for i in range(self.player_count or 0) if i != self.mpid]
        return all(self.others.get(i, {}).get("jump_state", 0) != packets.JUMPSTATE_NONE
                   for i in need)

    def _finish(self):
        self.func_active = False
        self.func_finished = True
        self._short_interval()                            # Task_PokemonJump_Member [:1252-1267]

    def _run_func(self):
        m = self.model
        if not self.func_active:
            m.update_own_jump()                           # UpdateGame runs every frame
            return
        self._func_frames += 1
        f = self.func_id
        if f == FUNC_GAME_INTRO:
            m.update_own_jump()
            if self._func_frames >= INTRO_FRAMES:
                m.allow_vine_updates = True
                m.reset_vine_state()                      # DoGameIntro case 6 [:1680-1688]
                self._finish()
        elif f == FUNC_WAIT_ROUND:
            m.update_own_jump()
            if self._func_frames >= 1:                    # AreLinkQueuesEmpty
                self._finish()
        elif f == FUNC_GAME_ROUND:
            before = (m.mon_state, m.jump_state)
            press = self.bot.should_jump(m)
            result = m.game_round_frame(press, self._others_hit())
            if press:
                self._short_interval()                    # [:1722-1727]
            if (m.mon_state, m.jump_state) != before and m.mon_state == packets.MONSTATE_HIT:
                self.hits += 1
                self._short_interval()
            elif m.jump_state != before[1]:
                self._short_interval()
            if result == "round_end":
                self.rounds_cleared += 1
                self._finish()
            elif result == "hit":
                self._finish()
        elif f == FUNC_GAME_OVER:
            if self._sub == 0:
                before = (m.mon_state, m.jump_state)
                if not m.update_vine_hit_states(self._others_hit()):
                    m.reset_vine_after_hit()
                if (m.mon_state, m.jump_state) != before:
                    self._short_interval()                # [:2434-2448]
                if self._all_jumped_or_hit():
                    self._sub = 1
                    self._func_frames = 0
            elif self._func_frames >= HIT_EFFECT_FRAMES:
                m.mon_state = m.prev_mon_state = packets.MONSTATE_NORMAL   # ResetPlayersMonState
                self.games += 1
                self._finish()
            m.update_own_jump()
        elif f == FUNC_ASK_PLAY_AGAIN:
            if self._func_frames >= PLAY_AGAIN_FRAMES:
                self.play_again_state = PLAY_AGAIN_YES if self.play_again else PLAY_AGAIN_NO
                self._finish()
        elif f == FUNC_RESET_GAME:
            if self._func_frames >= RESET_FRAMES:
                self._reset_for_new_game()
                self._finish()
        elif f == FUNC_GIVE_PRIZE:
            if self._func_frames >= PRIZE_FRAMES:
                self._finish()
        elif f == FUNC_SAVE:
            self._run_save()
        elif f == FUNC_EXIT:
            if self._func_frames == EXIT_FRAMES:
                self._on_exit_frames()

    def _run_save(self):
        """SavePokeJump [:1599-1636] -> Task_LinkFullSave: three standby rounds. Here the rounds
        are counted by whoever owns the radio (note_save_round)."""
        if self._save_rounds_left <= 0:
            self._finish()

    def note_save_round(self):
        """One Task_LinkFullSave standby round completed on every row."""
        if self.func_id == FUNC_SAVE and self._save_rounds_left > 0:
            self._save_rounds_left -= 1

    def _on_exit_frames(self):
        """ClosePokeJumpLink reached SetCloseLinkCallback [:1938-1942]: the radio owner's job."""

    def _reset_for_new_game(self):
        """ResetForNewGame [:914-951] for our player; the vine waits for GAME_INTRO."""
        m = self.model
        m.reset_for_new_game(m.rng_seed)
        m.allow_vine_updates = False
        self.bot.reset()                     # a plan made for the last game's vine timer is void
        for o in self.others.values():
            o["mon_state"] = packets.MONSTATE_NORMAL
            o["jump_state"] = packets.JUMPSTATE_NONE

    def member_state_packet(self):
        """SendPacket_MemberState [pokemon_jump.c:2823-2834]."""
        m = self.model
        return packets.encode_jump_member_state(
            mon_state=m.mon_state, jump_state=m.jump_state,
            func_finished=self.func_finished, jump_time_start=m.jump_time_start,
            func_id=self.func_id, play_again_state=self.play_again_state)

    def _member_state_words(self):
        """SendLinkData_Member: only when linkTimer is 0 [pokemon_jump.c:1269-1279]."""
        send = self.link_timer == 0 if self.link_timer_limit is not None else False
        if self.link_timer_limit is not None:
            self.link_timer = (self.link_timer + 1) & self.link_timer_limit
        if not send:
            return None
        return packets.send_words(self.member_state_packet())

    def run_frame(self):
        """Task_PokemonJump_Member for one VBlank, the radio aside -> this member's seven-word
        SEND_PACKET or None when its link timer is not due."""
        self._run_func()
        return self._member_state_words()


class PokeJumpMemberEngine(JumpMemberGame):
    """The PC as a wireless child of the Switch's Pokemon Jump group."""

    # what Sim reads
    ni_activity = ACTIVITY_POKEMON_JUMP
    ni_started = False        # SetHostRfuGameData(activity, 0, 0) [union_room.c:1159]
    in_seat_phase = False     # no seat walk: blocks are always allowed
    host_in_seat = False      # never hands the slot to linkstate's held keys

    def __init__(self, link_player=None, *, species=SPECIES_CHARMANDER, personality=0, ot_id=None,
                 trust_pia=False, play_again=True, log=lambda *a: None):
        self.lp = link_player or linkplayer.LinkPlayer()
        super().__init__(species=species, personality=personality,
                         ot_id=self.lp.trainer_id if ot_id is None else ot_id,
                         play_again=play_again, log=log)
        self.trust_pia = trust_pia

        self.state = ENTRY
        self.state_history = [ENTRY]
        self.rx = block.BlockReceiver()
        self.sender = None
        self.barrier = barriermod.BarrierResponder(log=self.log)
        self.leader_link_player = None
        self.link_players = {}
        self.mon_infos = {}
        self.done = False
        self.established = False

        self._post_mon_info = 0
        self._standby_ready = set()
        self.standby_rounds = []
        self.standby_count = 0        # gRfu.resendExitStandbyCount
        self._standby = None          # frames since our current standby round started

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_history.append(state)
            self.info(f"[jump] -> {state}")

    # --- inbound -------------------------------------------------------------------------------

    def feed_in_frame(self, unwrapped):
        if unwrapped is None:
            return
        completed, reqs = self.rx.feed_frame(unwrapped)
        rows = dict(unwrapped.get("positional", []))
        for mpid, slot in rows.items():
            rec = rfu.parse_slot(slot)
            if rec is None:
                continue
            if rec["op"] in (rfu.SEND_PLAYER_IDS, 0x7800) and mpid == 0:
                # Every SEND_PLAYER_IDS updates the count and our id [link_rfu_2.c:1134-1143].
                # Our RFU child slot is taken as 0: the PC is the only radio child here.
                count, mp = int.from_bytes(slot[2:4], "little"), slot[4 + CHILD_SLOT]
                if (count, mp) != (self.player_count, self.mpid):
                    self.player_count, self.mpid = count, mp
                    self.info(f"[jump] Leader announced {count} players; we are player "
                              f"{mp + 1} (multiplayer id {mp}).")
        for reqtype in reqs:
            self._on_req(reqtype)
        for mpid, count, data in completed:
            if self.state == ENTRY and mpid != self.mpid:
                parsed, ok = linkplayer.parse_block(data)
                if ok:
                    self.link_players[mpid] = parsed
                    if mpid == 0:
                        self.leader_link_player = parsed
                        self.info(f"[jump] Leader is {parsed.name!r}.")
        self._observe_barrier(unwrapped)
        if self.state == ENTRY and self._entry_complete():
            self.established = True
            self._start_standby()
            self._set_state(STANDBY)
        if self.state == MON_INFO:
            self._scan_mon_info(rows)
        elif self.state == PLAYING:
            self._scan_leader_state(rows)

    def _on_req(self, reqtype):
        if self.sender is not None and not self.sender.done:
            return
        size = 200 if reqtype == 0 else {1: 200, 2: 100, 3: 220, 4: 40}.get(reqtype, 200)
        data = linkplayer.build_block(self.lp).ljust(size, b"\x00")
        self.rx.peers[self.mpid or 1] = block.RecvBlock()
        self.sender = block.BlockSender(data, owner=self.mpid or 1, trust_pia=self.trust_pia)
        self.info(f"[jump] Sending our LinkPlayer block ({size} bytes).")

    def _entry_complete(self):
        if self.player_count is None or self.sender is not None and not self.sender.done:
            return False
        others = [i for i in range(self.player_count) if i != self.mpid]
        return self.sender is not None and all(i in self.link_players for i in others)

    def _start_standby(self):
        """Rfu_SetLinkStandbyCallback on a member: send READY_EXIT_STANDBY, then wait for every
        row [Rfu_LinkStandby, link_rfu_2.c:1564-1590]."""
        self._standby = 0
        self._standby_ready = set(i for i in self._standby_ready)   # latched flags survive

    def _observe_barrier(self, unwrapped):
        """gRfu.readyExitStandby[i] latches when row i shows READY_EXIT_STANDBY at our round
        [RfuHandleReceiveCommand, link_rfu_2.c:1178-1180]; the round completes once every player's
        flag is set, our own row included, and the count advances [:1535-1546]. The close-link
        handshake is the BarrierResponder's."""
        leader_close = None
        for mpid, slot in unwrapped.get("positional", []):
            rec = rfu.parse_slot(slot)
            if rec is None:
                continue
            if rec["op"] == rfu.READY_EXIT_STANDBY and rec.get("count") == self.standby_count:
                self._standby_ready.add(mpid)
            if mpid == 0 and rec["op"] == rfu.READY_CLOSE_LINK:
                leader_close = rec
        if self._standby is not None:
            need = set(range(self.player_count or 0))
            if need and need <= self._standby_ready:
                self.standby_rounds.append(self.standby_count)
                self.standby_count += 1
                self._standby_ready = set()
                self._standby = None
                self._on_standby_done()
        if leader_close is not None:
            self.barrier.on_in_slot(leader_close)

    def _on_standby_done(self):
        if self.state == STANDBY:
            self._set_state(MON_INFO)
        elif self.func_id == FUNC_SAVE and self._save_rounds_left > 0:
            self.note_save_round()
            self.info(f"[jump] Save standby {SAVE_STANDBY_ROUNDS - self._save_rounds_left}/"
                      f"{SAVE_STANDBY_ROUNDS} done.")

    def _standby_words(self):
        """First send, then a re-send every STANDBY_RESEND_FRAMES."""
        if self._standby is None:
            return None
        frames = self._standby
        self._standby += 1
        if frames % STANDBY_RESEND_FRAMES == 0:
            return rfu.exit_standby_words(self.standby_count)
        return None

    def _scan_mon_info(self, rows):
        for mpid, slot in rows.items():
            info = packets.decode_jump_mon_info(packets.packet_bytes_of_slot(slot))
            if info is not None and mpid not in self.mon_infos:
                self.mon_infos[mpid] = info
                self.info(f"[jump] Player {mpid + 1} brings species {info['species']}.")
        # Task_CommunicateMonInfo counts every row, our own reflection included, and keeps
        # sending until all have arrived [pokemon_jump.c:1994-2010].
        need = range(self.player_count or 0)
        if all(i in self.mon_infos for i in need) and self._post_mon_info == 0:
            self._post_mon_info = 1

    def _scan_leader_state(self, rows):
        """RecvLinkData_Member [pokemon_jump.c:1197-1237]: the leader's row and each other
        member's row are read independently."""
        others = {}
        for mpid, row in rows.items():
            if mpid in (0, self.mpid):
                continue
            ms = packets.decode_jump_member_state(packets.packet_bytes_of_slot(row))
            if ms is not None:          # RecvPacket_MemberStateToMember [:2862-2879]
                others[mpid] = ms
        slot = rows.get(0)
        state = packets.decode_jump_leader_state(packets.packet_bytes_of_slot(slot)) \
            if slot is not None else None
        self.observe_leader(state, others)

    # --- member functions: the radio's parts ---------------------------------------------------

    def _run_save(self):
        """SavePokeJump [:1599-1636] -> Task_LinkFullSave: three member-initiated standby rounds,
        counted when each completes [save.c:890-953]."""
        if self._standby is not None:
            return
        if self._save_rounds_left > 0:
            self._start_standby()
            return
        self._finish()

    def _on_exit_frames(self):
        self.barrier.initiate(barriermod.CLOSE)   # SetCloseLinkCallback [:1938-1942]
        self.info("[jump] Leaving the group (READY_CLOSE_LINK).")

    # --- outbound ------------------------------------------------------------------------------

    def poll_send_done(self):
        """Sim calls this instead of tick() on a VBlank whose slot the link pacer holds back
        [sim.py:_drive_reliable]. The console's game runs every VBlank regardless."""
        if self.state == PLAYING:
            self._run_func()
        if self.sender is not None and self.sender.state == block.HOLD:
            self.sender.tick(self.rx.peers[self.mpid or 1])

    def tick(self):
        if self.state == PLAYING:
            self._run_func()          # the game runs every frame, whatever goes on the wire
        if self.sender is not None and not self.sender.done:
            ack = self.rx.peers[self.mpid or 1]      # the leader's reflection of our block
            host_rx = self.rx.peers[0]
            words = self.sender.tick(ack, peer_sending=bool(host_rx.receiving and not host_rx.done))
            if words[0] & 0xFFFF:
                return words
        swords = self._standby_words()
        if swords is not None:
            return swords
        if self._standby is not None:
            return [0] * 7                       # waiting in the standby callback
        bwords = self.barrier.want_emit()        # close-link only
        if bwords is not None:
            return bwords
        if self.state == MON_INFO:
            if self._post_mon_info:
                self._post_mon_info += 1
                if self._post_mon_info > POST_MON_INFO_FRAMES:
                    self._set_state(PLAYING)
                    self.start_game()
                return [0] * 7
            # SendPacket_MonInfo every frame until all arrive [pokemon_jump.c:1996]
            return packets.send_words(self.mon_info_packet())
        if self.state == PLAYING:
            words = self._member_state_words()
            if words is not None:
                return words
        return [0] * 7
