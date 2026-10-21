"""Dodrio Berry Picking with the PC as the group leader (multiplayer id 0), the Switch as member 1
and virtual members 2..4 that exist only in the leader's command table (virtual_player.py).

The game itself is berrypick_game.DodrioLeaderGame (the leader's logic ported from the decomp);
this engine moves the session through the console's phases and puts each player's words on its
row. See docs/design.md.

The member console's path, in order:
  Task_PlayerExchange: SEND_PLAYER_IDS, then every player block-sends its LinkPlayerBlock after
      SEND_BLOCK_REQ [link_rfu_2.c:1813-1899]
  Task_RunScriptAndFadeToActivity: one standby round [union_room.c:1975-2010]
  Task_StartDodrioGame -> Task_CommunicateMonInfo: every player block-sends one byte (isShiny)
      [dodrio_berry_picking.c:809-830, 1453-1490], then one standby round [:831-843]
  DoCountdown: two standby rounds [:961-990]
  WaitGameStart: the member sends READY_START and plays [:1689-1696]; it then reads the leader's
      GameState from row 0 every frame [:1658-1687]
  WaitEndGame: after ten misses, READY_END until everyone is ready [:1047-1073]
  InitResults: every player block-sends berryResults (60 bytes); a member keeps the leader's,
      and its record comes from it [:1100-1157, 2633-2644]
  DoResults / AskPlayAgain: one-byte play-again blocks, a three-round link save when the best
      score is 3000 or more, then a new game or the close-link [:1162-1302]
"""

from collections import Counter, deque
from dataclasses import dataclass

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames import berrypick_game as bg, packets
from frlg_minigames.virtual_player import LINK_PLAYER_SEND_SIZE, VirtualPlayer
from frlg_minigames.net.gba import block, rfu

# BLOCK_REQ_* [include/link.h:111-115]: word0 low byte of a SEND_BLOCK_REQ (0 is 200 bytes).
BLOCK_REQ_SIZE_NONE = 0
# Blocks in a 200-byte send (ceil(200/12)).
COUNT_PARTY = 17

BP_LINK_PLAYER = "LINK_PLAYER"
BP_PREGAME = "PREGAME"
BP_PLAY = "PLAY"
BP_WAIT_END = "WAIT_END"
BP_RESULTS = "RESULTS"
BP_ASK = "ASK_PLAY_AGAIN"
BP_CLOSE = "CLOSE"
BP_DONE = "DONE"

MIN_PLAYERS = 3   # sLinkGroupMinMemberCounts[LINK_GROUP_BERRY_PICKING] [data/union_room.h:44-51]
MAX_PLAYERS = 5

STATUS_REPORT_FRAMES = 120

# PICK_NONE [dodrio_berry_picking.c]
PICK_NONE = 0


@dataclass(frozen=True)
class BerryPickTiming:
    # The console misses a single-VBlank SEND_PLAYER_IDS (host_mystery_gift.MysteryGiftTiming).
    player_ids_repeat_frames: int = 8
    standby_echo_frames: int = 4
    block_repeat: int = 2
    close_retry_frames: int = 60
    post_close_grace_frames: int = 5 * 60


DEFAULT_BERRY_PICK_TIMING = BerryPickTiming()


def default_virtual_link_player(host_lp, index):
    """A distinct trainer per virtual member: same game and language as the host."""
    return linkplayer.LinkPlayer(
        name=f"DODRIO{index}"[:7], trainer_id=(host_lp.trainer_id + 0x1111 * index) & 0xFFFFFFFF,
        version=host_lp.version, language=host_lp.language)


class HostBerryPickEngine:
    def __init__(self, *, link_player, num_players=MIN_PLAYERS, virtual_link_players=None,
                 trust_pia=True, timing=None, rng=None, log=lambda *a: None):
        if not MIN_PLAYERS <= num_players <= MAX_PLAYERS:
            raise ValueError(f"Dodrio Berry Picking takes {MIN_PLAYERS}..{MAX_PLAYERS} players")
        self.lp = link_player
        self.num_players = num_players
        self.trust_pia = trust_pia
        self.timing = timing or DEFAULT_BERRY_PICK_TIMING
        self.log = log
        self.info = getattr(log, "info", log)
        lps = list(virtual_link_players or [])
        while len(lps) < num_players - 2:
            lps.append(default_virtual_link_player(link_player, len(lps) + 1))
        self.virtuals = [VirtualPlayer(2 + i, lps[i], trust_pia=trust_pia,
                                       block_repeat=self.timing.block_repeat,
                                       standby_echo_frames=self.timing.standby_echo_frames,
                                       log=log)
                         for i in range(num_players - 2)]

        self.game = bg.DodrioLeaderGame(num_players, switch_ids=(1,), rng=rng)
        self.leader_bot = bg.BerryBot(self.game, 0, lag=bg.PICK_STATE_QUEUE_LEN + 1)
        self.virtual_bots = [bg.BerryBot(self.game, v.multiplayer_id, lag=1) for v in self.virtuals]
        self._last_sent = None
        self._row0_was_packet = False
        self._child_pick = None
        self._child_ready_end = False
        self.games_played = 0
        self.results = []          # (berries picked per player, scores) per finished game
        self.results_in_row = []   # berriesPickedInRow per finished game (five players only)
        self._play_again_round = 0
        self.child_play_again = None
        self.state = BP_LINK_PLAYER
        self.state_history = [self.state]
        self.trace = []
        self.done = False
        self.disconnect_requested = False
        self._close_confirmed = False
        self._close_grace_wait = None
        self._close_retry_wait = None

        self.child_link_player = None
        self.child_mon_info = None
        self.child_ready_to_start = False
        self.child_pick_state = PICK_NONE
        self.standby_rounds = []
        self._exit_count = 0
        self._answered_standby = set()
        self._rx = block.RecvBlock()
        self._words = deque()
        self._blocks = deque()
        self._sender = None
        self._mon_info_sent = False
        self._child_op_counts = Counter()
        self._child_mp_id = None
        self._parent_polls = 0
        self._status_countdown = STATUS_REPORT_FRAMES
        self._link_player_block = linkplayer.build_block(
            link_player, name_pad=linkplayer.HOST_NAME_PAD).ljust(LINK_PLAYER_SEND_SIZE, b"\x00")
        self._begin_link_player_exchange()

    # --- what HostSession and HostApplication read ---------------------------------------------

    @property
    def established(self):
        return self.child_link_player is not None

    @property
    def close_confirmed(self):
        return self._close_confirmed

    @property
    def reached_play(self):
        """Stage-1 gate passed: the console sent READY_START from WaitGameStart."""
        return self.child_ready_to_start

    def mark_disconnect_sent(self):
        if not self.disconnect_requested:
            raise RuntimeError("disconnect sent before the close-link handshake")
        self.done = True
        self._set_state(BP_DONE)

    def extra_rows(self):
        return [v.next_row() for v in self.virtuals]

    # --- internals -----------------------------------------------------------------------------

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_history.append(state)
            self.trace.append(("state", state))
            self.info(f"[berry] -> {state}")

    def _queue_words(self, words, label, repeat=1):
        for _ in range(max(1, repeat)):
            self._words.append(list(words))
        self.trace.append(("queue", label))

    def _queue_block(self, data, label):
        self._blocks.append((bytes(data), label))
        self.trace.append(("queue_block", label, len(data)))

    def _begin_link_player_exchange(self):
        """SEND_PLAYER_IDS with the table's player count, then exactly one SEND_BLOCK_REQ
        [Task_PlayerExchange, link_rfu_2.c:1832-1860]. The console in RFU slot 0 reads
        linkPlayerIdx[0] = 1 as its multiplayer id [link_rfu_2.c:1058-1066]."""
        ids = rfu.send_player_ids_words((1, 0, 0, 0), player_count=self.num_players)
        self._queue_words(ids, "SEND_PLAYER_IDS", self.timing.player_ids_repeat_frames)
        self._queue_words(rfu.send_block_req_words(BLOCK_REQ_SIZE_NONE), "BLOCK_REQ")
        self.info(f"[berry] Announced {self.num_players} players (the Switch is player 2; "
                  f"{', '.join(v.name for v in self.virtuals)} are virtual) and requested "
                  "LinkPlayer blocks.")

    def feed_child_slot(self, slot):
        if bytes(slot) == rfu.idle_slot():
            return
        rec = rfu.parse_slot(slot)
        if rec is None:
            return
        op = rec["op"]
        if op not in self._child_op_counts:
            self.info(f"[berry] Console sent its first {rec['name']} (state {self.state}).")
        self._child_op_counts[op] += 1
        if op == rfu.SEND_BLOCK_INIT:
            mp_id = (rec.get("owner_raw") or 0) & 0x7F
            if mp_id != self._child_mp_id:
                self._child_mp_id = mp_id
                self.info(f"[berry] Console reports multiplayer id {mp_id}.")
            if self.state == BP_LINK_PLAYER and mp_id != 1:
                ids = rfu.send_player_ids_words((1, 0, 0, 0), player_count=self.num_players)
                self._queue_words(ids, "SEND_PLAYER_IDS:repair",
                                  self.timing.player_ids_repeat_frames)
            self._rx.on_init(rec["count"], rec.get("owner_raw"))
        elif op == rfu.SEND_BLOCK:
            was_done = self._rx.done
            self._rx.on_block(rec["index"], rec["frag"])
            if self._rx.done and not was_done:
                data, count = self._rx.data(), self._rx.count
                self._rx.consume()
                self._on_child_block(count, data)
        elif op == rfu.READY_EXIT_STANDBY:
            self._on_child_standby(rec.get("count", 0))
        elif op == rfu.READY_CLOSE_LINK:
            self._on_child_close(rec.get("count", 0))
        elif op == rfu.SEND_PACKET:
            self._on_child_packet(packets.bytes_from_words(rec["packet"]))

    def _on_child_block(self, count, data):
        if self.state == BP_LINK_PLAYER and self.child_link_player is None:
            if count != COUNT_PARTY:
                self.trace.append(("unexpected_block", count))
                return
            parsed, ok = linkplayer.parse_block(data)
            if not ok:
                self.info("[berry] Console LinkPlayer block failed the GameFreak check; waiting.")
                return
            self.child_link_player = parsed
            self._queue_block(self._link_player_block, "host:link_player")
            for v in self.virtuals:
                v.queue_block(v.link_player_block(), "link_player")
            self.info(f"[berry] Console is {parsed.name!r}; sending the leader's and "
                      f"{len(self.virtuals)} virtual LinkPlayer block(s).")
            self._set_state(BP_PREGAME)
            return
        if self.state == BP_PREGAME and not self._mon_info_sent and count == 1:
            # SendBlock(0, &monInfo.isShiny, 1) [dodrio_berry_picking.c:1462]
            self.child_mon_info = {"is_shiny": bool(data[0])}
            self._mon_info_sent = True
            self._queue_block(b"\x00", "host:mon_info")
            for v in self.virtuals:
                v.queue_block(b"\x00", "mon_info")
            self.info("[berry] Console sent its Dodrio info; answering for the leader and the "
                      "virtual members.")
            return
        if self.state in (BP_WAIT_END, BP_RESULTS) and count == block.frag_count(bg.RESULTS_BLOCK_SIZE):
            # InitResults_Member's berryResults send [:1124-1128]; the leader's went out already.
            self.trace.append(("child_results_block",))
            return
        if self.state == BP_RESULTS and count == 1:
            # DoResults case 1 [:1180-1186]: everyone sends GetPlayAgainState(), still NONE.
            self._queue_block(bytes([bg.PLAY_AGAIN_NONE]), "host:results_ack")
            for v in self.virtuals:
                v.queue_block(bytes([bg.PLAY_AGAIN_NONE]), "results_ack")
            self._set_state(BP_ASK)
            return
        if self.state == BP_ASK and count == 1:
            # AskPlayAgain case 4 [:1225-1233]: the console's answer.
            self.child_play_again = data[0]
            self._queue_block(bytes([bg.PLAY_AGAIN_YES]), "host:play_again")
            for v in self.virtuals:
                v.queue_block(bytes([bg.PLAY_AGAIN_YES]), "play_again")
            if data[0] == bg.PLAY_AGAIN_YES:
                self.info("[berry] The Switch player chose to play again.")
                self._reset_game()
            else:
                self.info("[berry] The Switch player is done; closing the link.")
                self._begin_end_link()
            return
        self.trace.append(("unexpected_block", self.state, count))

    def _reset_game(self):
        """ResetGame -> InitDodrioGame, SetRandomPrize [:1376-1420]; the console then runs its
        countdown standbys and READY_START again."""
        self.game.init_game()
        self.game.set_random_prize()
        self.child_ready_to_start = False
        self._child_ready_end = False
        self._last_sent = None
        self._row0_was_packet = False
        for v in self.virtuals:
            v.set_packet(None)
        self._set_state(BP_PREGAME)

    def _begin_end_link(self):
        """EndLink: SetCloseLinkCallback [:1305-1325]."""
        self._set_state(BP_CLOSE)
        self._queue_words(rfu.close_link_words(self._exit_count), "READY_CLOSE_LINK",
                          self.timing.standby_echo_frames)
        for v in self.virtuals:
            v.answer_close(self._exit_count)
        self._close_retry_wait = self.timing.close_retry_frames

    def _on_child_standby(self, count):
        """Rfu_LinkStandby: the console waits for READY_EXIT_STANDBY at its own round count on
        every row below playerCount [link_rfu_2.c:1522-1548]; the leader's goes last in the
        original order, which the console does not check."""
        self._exit_count = max(self._exit_count, count + 1)
        if count in self._answered_standby:
            return
        self._answered_standby.add(count)
        self.standby_rounds.append((self.state, count))
        for v in self.virtuals:
            v.answer_standby(count)
        self._queue_words(rfu.exit_standby_words(count), f"STANDBY:{count}",
                          self.timing.standby_echo_frames)
        self.info(f"[berry] Standby round {count} answered on every row "
                  f"({len(self.standby_rounds)} so far).")

    def _on_child_packet(self, data):
        ready = packets.decode_berry_ready_start(data)
        if ready is not None:
            if ready and not self.child_ready_to_start and self.state == BP_PREGAME:
                self.child_ready_to_start = True
                # AllPlayersReadyToStart lets the retail leader start at once [:2235-2253];
                # InitCountdown's first wave [:1871-1883].
                self.game.init_first_wave()
                for v in self.virtuals:
                    v.set_packet(None)
                self.info("[berry] Console is at the start line (READY_START); berries fall.")
                self._set_state(BP_PLAY)
            return
        pick = packets.decode_berry_pick_state(data)
        if pick is not None:
            self.child_pick_state = pick
            self._child_pick = pick
            return
        if packets.decode_berry_ready_end(data):
            self._child_ready_end = True

    def _on_child_close(self, count):
        self._exit_count = max(self._exit_count, count)
        for v in self.virtuals:
            v.answer_close(count)
        if self.state != BP_CLOSE:
            self._set_state(BP_CLOSE)
            self._queue_words(rfu.close_link_words(self._exit_count), "READY_CLOSE_LINK",
                              self.timing.standby_echo_frames)
            self._close_retry_wait = self.timing.close_retry_frames
        if not self._close_confirmed:
            self._close_confirmed = True
            self._close_grace_wait = self.timing.post_close_grace_frames
            self.info("[berry] Console asked to close the link; holding the network open while it "
                      "finishes.")

    def _game_frame(self, sent=True):
        """One leader VBlank of PLAY_GAME or WAIT_END_GAME; -> the GameState packet the leader
        sends this frame. sent=False: the link held this VBlank's row back, so row 0 (the
        leader's loopback) keeps the last packet that went out."""
        g = self.game
        picks = {}
        if self._child_pick not in (None, PICK_NONE):
            picks[1] = self._child_pick
        self._child_pick = None
        ready = []
        if g.func == bg.FUNC_PLAY_GAME:
            for v, bot in zip(self.virtuals, self.virtual_bots):
                choice = bot.choose()
                if choice != PICK_NONE:
                    picks[v.multiplayer_id] = choice
                    # SendLinkData_Member sends PICK_STATE while a pick is held [:1698-1700]
                    v.queue_words(packets.send_words(packets.encode_berry_pick_state(choice)))
            leader_pick = self.leader_bot.choose()
        else:
            leader_pick = PICK_NONE
            if self._child_ready_end:
                ready.append(1)
            for v in self.virtuals:
                # SendLinkData_Member in WAIT_END_GAME [:1701-1704]
                if not g.berries_falling and not g.all_ready_to_end:
                    ready.append(v.multiplayer_id)
                    v.set_packet(packets.encode_berry_ready_end(True))
        before = g.func
        packet = g.leader_frame(self._last_sent, picks, leader_pick,
                                row0_is_packet=self._row0_was_packet, ready_ids=ready)
        if before == bg.FUNC_PLAY_GAME and g.func == bg.FUNC_WAIT_END_GAME:
            self._set_state(BP_WAIT_END)
            self.info(f"[berry] Ten misses: game over. Berries so far: "
                      f"{[g.berries_picked(i) for i in range(self.num_players)]}.")
        elif g.func == bg.FUNC_INIT_RESULTS and self.state == BP_WAIT_END:
            self._finish_game()
        if sent:
            self._last_sent = packet
        return packet

    def idle_frame(self):
        """HostSession calls this on a VBlank whose row the reliable window holds back. The
        original leader runs its game and sends every VBlank, so the leader's own state carries
        on as if the GameState went out; the console only misses this one update."""
        if self.state in (BP_PLAY, BP_WAIT_END):
            packet = self._game_frame()
            self._row0_was_packet = packet is not None

    def _finish_game(self):
        """InitResults_Leader: send berryResults [:1068-1098]."""
        g = self.game
        picked = [g.berries_picked(i) for i in range(self.num_players)]
        scores = [g.score(i) for i in range(self.num_players)]
        self.results.append((picked, scores))
        self.results_in_row.append(g.max_berries_picked_in_row)
        self.games_played += 1
        self.info(f"[berry] Game {self.games_played} over. Berries: {picked}; scores: {scores}. "
                  f"The Switch player (player 2) picked {picked[1]}.")
        block_data = g.results_block()
        self._queue_block(block_data, "host:results")
        for v in self.virtuals:
            v.set_packet(None)
            v.queue_block(block_data, "results")
        self._set_state(BP_RESULTS)

    def _report_status(self):
        ops = ", ".join(f"{rfu.RFUCMD_NAMES.get(op, hex(op))}x{n}"
                        for op, n in self._child_op_counts.most_common()) or "none"
        self.info(f"[berry] {self.state}: console {'known' if self.established else 'unknown'}, "
                  f"mpId {self._child_mp_id}, standby rounds {[c for _, c in self.standby_rounds]}, "
                  f"virtual blocks sent {[v.blocks_sent for v in self.virtuals]}, "
                  f"console opcodes: {ops}; row-one echo backlog "
                  f"{getattr(self, 'echo_backlog', 0)}, dropped {getattr(self, 'echo_dropped', 0)}")

    def _tick_close(self):
        if self._close_grace_wait is not None:
            self._close_grace_wait -= 1
            if self._close_grace_wait <= 0:
                self._close_grace_wait = None
                self.disconnect_requested = True
                return
        if self._close_retry_wait is not None:
            self._close_retry_wait -= 1
            if self._close_retry_wait <= 0:
                self._queue_words(rfu.close_link_words(self._exit_count), "READY_CLOSE_LINK",
                                  self.timing.standby_echo_frames)
                self._close_retry_wait = self.timing.close_retry_frames

    def tick(self):
        """One VBlank: the leader's own row (row 0). The game runs every frame; the row carries a
        queued command or block fragment first, else the GameState."""
        self._parent_polls += 1
        self._status_countdown -= 1
        if self._status_countdown <= 0 and self.state != BP_DONE:
            self._status_countdown = STATUS_REPORT_FRAMES
            self._report_status()
        if self.state == BP_CLOSE and not self.disconnect_requested:
            self._tick_close()
        packet = self._game_frame() if self.state in (BP_PLAY, BP_WAIT_END) else None
        words = self._row0_words(packet)
        # Row 0 of the next frame is this frame's gSendCmd [link_rfu_2.c:759-767]
        self._row0_was_packet = (words[0] & rfu.RFUCMD_MASK) == rfu.SEND_PACKET
        return words

    def _row0_words(self, packet):
        if self._words:
            return self._words.popleft()
        if self._sender is None and self._blocks:
            data, label = self._blocks.popleft()
            self._sender = block.BlockSender(data, owner=0, trust_pia=self.trust_pia,
                                             stream_repeat=self.timing.block_repeat)
            self.trace.append(("send_block", label))
        if self._sender is not None:
            words = self._sender.tick(None)
            if self._sender.done:
                self._sender = None
            if words[0] & 0xFFFF:
                return words
        if packet is not None:
            # SendLinkData_Leader: GameState every frame of PLAY_GAME and WAIT_END_GAME [:1629-1656]
            return packets.send_words(packet)
        return [0] * 7
