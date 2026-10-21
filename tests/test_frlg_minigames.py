"""Joyful Game Corner minigames: packet layouts, virtual rows, and the Dodrio leader's stage 1
against a model of the member console's link code (docs/frlg_minigames.md)."""

import pytest

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames import packets
from frlg_minigames.berrypick_host import (
    BP_LINK_PLAYER, BP_PLAY, BP_PREGAME, HostBerryPickEngine)
from frlg_minigames.virtual_player import VirtualPlayer
from frlg_minigames.net.gba import block, gbaframe, ni, rfu
from frlg_minigames.session import MinigameRFULeader
from tests.oracle.c_extract import decomp_src as _decomp_src


# --- packet layouts --------------------------------------------------------------------------

def test_jump_mon_info_layout():
    data = packets.encode_jump_mon_info(0x0019, 0x11223344, 0xAABBCCDD)
    assert data == bytes([1, 0, 0x19, 0, 0x44, 0x33, 0x22, 0x11, 0xDD, 0xCC, 0xBB, 0xAA])
    assert packets.decode_jump_mon_info(data) == {
        "species": 0x19, "personality": 0x11223344, "ot_id": 0xAABBCCDD}


def test_jump_leader_state_bitfields():
    data = packets.encode_jump_leader_state(
        func_id=2, mon_state=1, received_bonus_flags=0b10101, jump_state=2,
        jump_time_start=0x1234, data=0xBEEF, jumps_in_row=0x7FFF, jump_score=0x1FFFF)
    assert data[:4] == bytes([3, 2, 1, 0b010_10101])
    assert data[4:8] == bytes([0x34, 0x12, 0xEF, 0xBE])
    assert int.from_bytes(data[8:12], "little") == 0xFFFFFFFF
    back = packets.decode_jump_leader_state(data)
    assert back["jumps_in_row"] == 0x7FFF and back["jump_score"] == 0x1FFFF
    assert back["received_bonus_flags"] == 0b10101 and back["jump_state"] == 2
    small = packets.decode_jump_leader_state(packets.encode_jump_leader_state(
        func_id=0, mon_state=0, received_bonus_flags=0, jump_state=0, jump_time_start=0,
        data=0, jumps_in_row=200, jump_score=1))
    assert (small["jumps_in_row"], small["jump_score"]) == (200, 1)


def test_jump_member_state_layout():
    data = packets.encode_jump_member_state(mon_state=1, jump_state=1, func_finished=True,
                                            jump_time_start=0x0102, func_id=4,
                                            play_again_state=0x0506)
    assert data == bytes([4, 1, 1, 1, 0x02, 0x01, 4, 0, 0x06, 0x05, 0, 0])
    assert packets.decode_jump_member_state(data)["play_again_state"] == 0x0506


def test_berry_aligned_fields_sit_at_offset_four():
    assert packets.encode_berry_ready_start(True) == bytes([1, 0, 0, 0, 1]) + bytes(7)
    assert packets.encode_berry_pick_state(2) == bytes([3, 0, 0, 0, 2]) + bytes(7)
    assert packets.encode_berry_ready_end(True) == bytes([4, 0, 0, 0, 1, 0, 0, 0]) + bytes(4)


def test_berry_game_state_round_trip_and_size():
    state = dict(fall_dist=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], berry_ids=[0, 1, 2, 3, 0, 1, 2, 3, 1, 2],
                 pick_states=[1, 2, 3, 0, 1], ate_berry=[True, False, True, False, True],
                 missed_berry=[False, True, False, True, True], num_gray_squares=10,
                 berries_falling=True, all_ready_to_end=False)
    data = packets.encode_berry_game_state(**state)
    assert len(data) == packets.PACKET_BYTES and data[0] == packets.BERRY_PACKET_GAME_STATE
    assert data[1] == 0x21
    back = packets.decode_berry_game_state(data)
    for key, value in state.items():
        assert back[key] == value, key


def test_send_packet_row_round_trip():
    data = packets.encode_berry_pick_state(1)
    slot = rfu.serialize(packets.send_words(data))
    assert packets.packet_bytes_of_slot(slot) == data
    assert packets.packet_bytes_of_slot(rfu.idle_slot()) is None


# --- virtual rows ----------------------------------------------------------------------------

def _collect_block(rows):
    rx = block.RecvBlock()
    owner = None
    for row in rows:
        rec = rfu.parse_slot(row)
        if rec is None:
            continue
        if rec["op"] == rfu.SEND_BLOCK_INIT:
            rx.on_init(rec["count"], rec["owner_raw"])
            owner = rec["owner_raw"]
        elif rec["op"] == rfu.SEND_BLOCK:
            rx.on_block(rec["index"], rec["frag"])
            if rx.done:
                return owner, rx.data()
    return owner, None


def test_virtual_player_sends_a_valid_link_player_block_as_its_own_id():
    v = VirtualPlayer(2, linkplayer.LinkPlayer(name="BOT"))
    v.queue_block(v.link_player_block())
    owner, data = _collect_block([v.next_row() for _ in range(80)])
    assert owner == 0x82 and len(data) == 17 * 12
    parsed, ok = linkplayer.parse_block(data)
    assert ok and parsed.name == "BOT"


def test_virtual_player_answers_standby_with_the_consoles_round():
    v = VirtualPlayer(3, linkplayer.LinkPlayer())
    v.answer_standby(7)
    rec = rfu.parse_slot(v.next_row())
    assert rec["op"] == rfu.READY_EXIT_STANDBY and rec["count"] == 7


def test_rfu_leader_publishes_extra_rows_and_keeps_two_player_table_unchanged():
    def uni_leader(rows=None):
        leader = MinigameRFULeader(host_session_id=b"\x01\xf1", skip_parent_ni=True)
        leader.receive(gbaframe.build_connect(b"\x80\x84"))
        leader.tick()
        leader.tick()
        child = ni.NISender(ni.build_game_data(5, 0x2288, "EMU"))
        ts = 1
        while not child.done:
            leader.receive(gbaframe.wrap_t(child.next_slot(), ts))
            ts += 1
            while leader._pending:
                leader.tick()
        assert leader.state == "UNI"
        if rows is not None:
            leader.extra_rows = lambda: rows
        return leader

    plain = uni_leader().tick([0x2F00, 1, 2, 3, 4, 5, 6])
    same = uni_leader([]).tick([0x2F00, 1, 2, 3, 4, 5, 6])
    assert plain == same
    extra = rfu.serialize(rfu.exit_standby_words(3))
    frame = uni_leader([extra]).tick([0x2F00, 1, 2, 3, 4, 5, 6])
    assert frame != plain
    assert extra in frame and frame.endswith(bytes(rfu.COMM_SLOT_LENGTH * 2))


# --- the Dodrio leader against a model of the member console --------------------------------

class MemberConsoleModel:
    """The member side of link_rfu_2.c and dodrio_berry_picking.c, reduced to what the leader's
    rows must satisfy: SEND_PLAYER_IDS, every row's blocks, every row's standby at the console's
    own round, then READY_START. Its own row reaches the table through the leader's echo."""

    def __init__(self, link_player):
        self.lp = link_player
        self.player_count = 0
        self.mp_id = 0
        self.round = 0
        self.stage = "player_ids"
        self.sender = None
        self.rx = {}
        self.blocks = {}
        self.ready_rows = set()
        self.link_players = {}
        self.history = []

    def _start_block(self, data):
        self.sender = block.BlockSender(data, owner=self.mp_id, trust_pia=True)
        self.rx = {i: block.RecvBlock() for i in range(5)}
        self.blocks = {}

    def feed_table(self, rows):
        for i, row in enumerate(rows):
            rec = rfu.parse_slot(row)
            if rec is None:
                continue
            op = rec["op"]
            if op == rfu.SEND_PLAYER_IDS and self.stage == "player_ids":
                self.player_count = rec_word(row, 1)
                self.mp_id = row[4]                   # linkPlayerIdx[childSlot 0]
                self.stage = "block_req"
            elif op == rfu.SEND_BLOCK_REQ and self.stage == "block_req":
                self._start_block(linkplayer.build_block(self.lp).ljust(200, b"\x00"))
                self.stage = "link_players"
            elif op == rfu.SEND_BLOCK_INIT and i in self.rx and i not in self.blocks:
                self.rx[i].on_init(rec["count"], rec["owner_raw"])
            elif op == rfu.SEND_BLOCK and i in self.rx and i not in self.blocks:
                self.rx[i].on_block(rec["index"], rec["frag"])
                if self.rx[i].done:
                    self.blocks[i] = self.rx[i].data()
            elif op == rfu.READY_EXIT_STANDBY and rec["count"] == self.round:
                self.ready_rows.add(i)
        n = self.player_count
        if self.stage == "link_players" and len([i for i in self.blocks if i < n]) == n:
            for i in range(n):
                parsed, ok = linkplayer.parse_block(self.blocks[i])
                assert ok, f"row {i} LinkPlayer block fails the GameFreak check"
                self.link_players[i] = parsed
            self._advance("standby:fade")
        elif self.stage.startswith("standby") and all(i in self.ready_rows for i in range(n)):
            self.ready_rows.clear()
            self.round += 1
            nxt = {"standby:fade": "mon_info", "standby:mon_info": "standby:countdown1",
                   "standby:countdown1": "standby:countdown2",
                   "standby:countdown2": "ready_start"}[self.stage]
            if nxt == "mon_info":
                self._start_block(b"\x00")
            self._advance(nxt)
        elif self.stage == "mon_info" and len([i for i in self.blocks if i < n]) == n:
            self._advance("standby:mon_info")

    def _advance(self, stage):
        self.history.append(stage)
        self.stage = stage
        self.ready_rows.clear()

    def next_cmd(self):
        if self.sender is not None and not self.sender.done:
            return rfu.serialize(self.sender.tick(None))
        if self.stage.startswith("standby"):
            return rfu.serialize(rfu.exit_standby_words(self.round))
        if self.stage == "ready_start":
            return rfu.serialize(packets.send_words(packets.encode_berry_ready_start(True)))
        return rfu.idle_slot()


def rec_word(row, index):
    return int.from_bytes(row[2 * index:2 * index + 2], "little")


@pytest.mark.parametrize("num_players", [3, 4, 5])
def test_dodrio_leader_walks_the_member_console_to_the_game_screen(num_players):
    host_lp = linkplayer.LinkPlayer(name="LEADER")
    engine = HostBerryPickEngine(link_player=host_lp, num_players=num_players)
    console = MemberConsoleModel(linkplayer.LinkPlayer(name="SWITCH"))
    assert engine.state == BP_LINK_PLAYER
    child_cmd = rfu.idle_slot()
    for _ in range(3000):
        table = [rfu.serialize(engine.tick()), child_cmd] + engine.extra_rows()
        console.feed_table(table)
        child_cmd = console.next_cmd()
        engine.feed_child_slot(child_cmd)
        if engine.state == BP_PLAY:
            break
    assert engine.state == BP_PLAY, (engine.state, console.stage, console.history)
    assert console.player_count == num_players and console.mp_id == 1
    assert [console.link_players[i].name for i in (0, 1)] == ["LEADER", "SWITCH"]
    assert all(console.link_players[i].name.startswith("DODRIO") for i in range(2, num_players))
    assert console.history == ["standby:fade", "mon_info", "standby:mon_info",
                               "standby:countdown1", "standby:countdown2", "ready_start"]
    assert engine.child_link_player.name == "SWITCH"
    assert [c for _, c in engine.standby_rounds] == [0, 1, 2, 3]
    # In play, once its queued answers drain, the leader's row carries GameState every frame.
    rows = [rfu.serialize(engine.tick()) for _ in range(12)]
    states = [packets.decode_berry_game_state(packets.packet_bytes_of_slot(r)) for r in rows]
    assert states[-1] is not None and all(s is not None for s in states[states.index(states[-1]):])


def test_dodrio_leader_rejects_impossible_player_counts():
    with pytest.raises(ValueError):
        HostBerryPickEngine(link_player=linkplayer.LinkPlayer(), num_players=2)


# --- Pokemon Jump: the PC as a member under a model of the leader console --------------------

from frlg_minigames import pokejump_member as pj  # noqa: E402


class LeaderConsoleModel:
    """The leader side of link_rfu_2.c and pokemon_jump.c for two players, reduced to the
    hand-shakes a member must satisfy: player exchange, the member-initiated standby, MonInfo,
    then GameIntro -> WaitRound once the member reports funcFinished."""

    def __init__(self, lp):
        self.lp = lp
        self.frame = 0
        self.queue = [rfu.send_player_ids_words((1, 0, 0, 0), player_count=2)] * 4 \
            + [rfu.send_block_req_words(0)]
        self.sender = None
        self.rx = block.RecvBlock()
        self.member_lp = None
        self.round = 0
        self.member_ready = False
        self.stage = "exchange"
        self.member_mon = None
        self.func_id = pj.FUNC_GAME_INTRO
        self.intro_left = 90
        self.history = []

    def feed_member(self, slot):
        rec = rfu.parse_slot(slot)
        if rec is None:
            return
        if rec["op"] == rfu.SEND_BLOCK_INIT:
            self.rx.on_init(rec["count"], rec["owner_raw"])
        elif rec["op"] == rfu.SEND_BLOCK:
            self.rx.on_block(rec["index"], rec["frag"])
            if self.rx.done and self.member_lp is None:
                parsed, ok = linkplayer.parse_block(self.rx.data())
                assert ok
                self.member_lp = parsed
        elif rec["op"] == rfu.READY_EXIT_STANDBY and rec["count"] == self.round:
            self.member_ready = True
        elif rec["op"] == rfu.SEND_PACKET:
            data = packets.bytes_from_words(rec["packet"])
            mon = packets.decode_jump_mon_info(data)
            if mon is not None:
                self.member_mon = mon
            ms = packets.decode_jump_member_state(data)
            if ms is not None and self.stage == "game":
                if ms["func_finished"] and ms["func_id"] == self.func_id and self.intro_left <= 0 \
                        and self.func_id == pj.FUNC_GAME_INTRO:
                    self.func_id = pj.FUNC_WAIT_ROUND
                    self.history.append("WAIT_ROUND")

    def row0(self):
        self.frame += 1
        if self.queue:
            return rfu.serialize(self.queue.pop(0))
        if self.stage == "exchange":
            if self.sender is None:
                self.sender = block.BlockSender(linkplayer.build_block(self.lp).ljust(200, b"\0"),
                                                owner=0, trust_pia=True)
            words = self.sender.tick(None)
            if self.sender.done and self.member_lp is not None:
                self.stage = "standby"
                self.history.append("standby")
            return rfu.serialize(words)
        if self.stage == "standby":
            if self.member_ready:
                self.stage = "mon_info"
                self.history.append("mon_info")
                return rfu.serialize(rfu.exit_standby_words(self.round))
            return rfu.idle_slot()
        if self.stage == "mon_info":
            if self.member_mon is not None:
                self.stage = "game"
                self.history.append("game")
            return rfu.serialize(packets.send_words(packets.encode_jump_mon_info(25, 1, 2)))
        self.intro_left -= 1
        return rfu.serialize(packets.send_words(packets.encode_jump_leader_state(
            func_id=self.func_id, mon_state=0, received_bonus_flags=0, jump_state=0,
            jump_time_start=0, data=0x1234, jumps_in_row=0, jump_score=0)))


def test_pokejump_member_follows_the_leader_console_to_the_first_round():
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"), trust_pia=True)
    leader = LeaderConsoleModel(linkplayer.LinkPlayer(name="SWITCH"))
    member_slot = rfu.idle_slot()
    for _ in range(3000):
        table = {"positional": [(0, leader.row0()), (1, member_slot)]}
        member.feed_in_frame(table)
        member_slot = rfu.serialize(member.tick())
        leader.feed_member(member_slot)
        if member.reached_round:
            break
    assert member.reached_round, (member.state, leader.stage, leader.history)
    assert member.mpid == 1 and member.player_count == 2
    assert member.leader_link_player.name == "SWITCH"
    assert leader.member_lp.name == "BOT"
    assert leader.member_mon == {"species": pj.SPECIES_CHARMANDER, "personality": 0,
                                 "ot_id": member.lp.trainer_id}
    assert leader.history == ["standby", "mon_info", "game", "WAIT_ROUND"]
    assert member.state_history == [pj.ENTRY, pj.STANDBY, pj.MON_INFO, pj.PLAYING]


class RoundLeaderModel:
    """A leader that has finished the intro: it alternates WAIT_ROUND (with its vine timer) and
    GAME_ROUND, each time waiting for the member's funcFinished on that function
    [RecvLinkData_Leader, pokemon_jump.c:1114-1137; GameRound_Leader :1358-1376]. Its own
    Pokemon never touches the vine here; only the member's reports can end the game."""

    def __init__(self, seed, link_frames):
        from frlg_minigames.pokejump_model import JumpModel
        self.vine = JumpModel()
        self.vine.rng_seed = seed
        self.vine.init_vine_state()
        self.vine.reset_vine_state()
        self.vine.allow_vine_updates = True
        self.func_id = pj.FUNC_GAME_INTRO
        self.data = seed
        self.link_frames = link_frames
        self.pending = []           # (deliver_at, member packet) to model radio latency
        self.frame = 0
        self.member_hit = False
        self.rounds = 0

    def feed_member(self, slot):
        data = packets.packet_bytes_of_slot(slot)
        if data is not None:
            self.pending.append((self.frame + self.link_frames, data))

    def _member_packets(self):
        ready, keep = [], []
        for at, data in self.pending:
            if at <= self.frame:
                ready.append(data)
            else:
                keep.append((at, data))
        self.pending = keep
        return ready

    def row0(self):
        self.frame += 1
        for data in self._member_packets():
            ms = packets.decode_jump_member_state(data)
            if ms is None:
                continue
            if ms["mon_state"] == packets.MONSTATE_HIT:
                self.member_hit = True
            if ms["func_finished"] and ms["func_id"] == self.func_id:
                if self.func_id in (pj.FUNC_GAME_INTRO, pj.FUNC_GAME_ROUND):
                    if self.func_id == pj.FUNC_GAME_ROUND:
                        self.rounds += 1
                    self.func_id = pj.FUNC_WAIT_ROUND
                    self.data = self.vine.vine_timer
                elif self.func_id == pj.FUNC_WAIT_ROUND:
                    self.func_id = pj.FUNC_GAME_ROUND
        if self.func_id == pj.FUNC_GAME_ROUND:
            self.vine.update_vine_state()
        return rfu.serialize(packets.send_words(packets.encode_jump_leader_state(
            func_id=self.func_id, mon_state=0, received_bonus_flags=0, jump_state=0,
            jump_time_start=0, data=self.data, jumps_in_row=self.rounds, jump_score=0)))


@pytest.mark.parametrize("seed,link_frames", [(0x1234, 1), (0xBEEF, 2), (7, 4)])
def test_pokejump_bot_clears_two_hundred_swings_by_the_games_own_rules(seed, link_frames):
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"))
    member.mpid, member.player_count = 1, 2
    member.state = pj.PLAYING
    leader = RoundLeaderModel(seed, link_frames)
    member.leader_state = packets.decode_jump_leader_state(packets.encode_jump_leader_state(
        func_id=0, mon_state=0, received_bonus_flags=0, jump_state=0, jump_time_start=0,
        data=seed, jumps_in_row=0, jump_score=0))
    member.model.init_vine_state()
    member._set_func(pj.FUNC_GAME_INTRO)
    from collections import deque
    downlink = deque([rfu.idle_slot()] * link_frames)     # leader -> member delay as well
    for _ in range(60 * 60 * 30):
        downlink.append(leader.row0())
        member.feed_in_frame({"positional": [(0, downlink.popleft())]})
        leader.feed_member(rfu.serialize(member.tick()))
        if leader.rounds >= 210 or leader.member_hit:
            break
    assert not leader.member_hit and member.hits == 0
    assert leader.rounds >= 210 and member.rounds_cleared >= 210


# --- a whole Dodrio game: the member console plays, the PC leads -----------------------------

import random  # noqa: E402

from frlg_minigames import berrypick_game as bg  # noqa: E402
from frlg_minigames import berrypick_tables as BT  # noqa: E402
from frlg_minigames.berrypick_host import BP_ASK, BP_CLOSE, BP_RESULTS, BP_WAIT_END  # noqa: E402,F401


class PlayingMemberConsole(MemberConsoleModel):
    """MemberConsoleModel continued through PlayGame_Member, WaitEndGame_Member, InitResults,
    DoResults, AskPlayAgain (with Task_LinkFullSave's three standby rounds when the best score is
    PRIZE_SCORE or more) and either ResetGame's countdown or EndLink [dodrio_berry_picking.c
    :1015-1325; save.c:882-962]. It presses a direction when one of its berries is in range (with
    probability `skill` per frame) and counts the berries the leader's GameState says it ate.
    `answers` is its play-again answer per game."""

    def __init__(self, lp, num_players, skill, answers, seed):
        super().__init__(lp)
        self.n = num_players
        self.skill = skill
        self.answers = list(answers)
        self.rng = random.Random(seed)
        self.state = None
        self.ate_seen = []
        self._prev_ate = False
        self.result_blocks = []
        self.leader_answers = []
        self.saves = 0
        self.closed = False
        self._save_left = 0

    def _block_stage(self, data, stage):
        self._start_block(data)
        self._advance(stage)

    def feed_table(self, rows):
        own = ("play", "wait_end", "results", "results_ack", "save", "ask", "close",
               "again1", "again2")
        if self.stage not in own:
            super().feed_table(rows)
            if self.stage == "ready_start" and packets.decode_berry_game_state(
                    packets.packet_bytes_of_slot(rows[0])) is not None:
                self.ate_seen.append(0)
                self._prev_ate = False
                self._advance("play")
            return
        n = self.n
        for i, row in enumerate(rows):
            rec = rfu.parse_slot(row)
            if rec is None:
                continue
            if rec["op"] == rfu.SEND_BLOCK_INIT and i not in self.blocks:
                self.rx[i].on_init(rec["count"], rec["owner_raw"])
            elif rec["op"] == rfu.SEND_BLOCK and i not in self.blocks:
                self.rx[i].on_block(rec["index"], rec["frag"])
                if self.rx[i].done:
                    self.blocks[i] = self.rx[i].data()
            elif rec["op"] == rfu.READY_CLOSE_LINK and i == 0:
                self.closed = True
            elif rec["op"] == rfu.READY_EXIT_STANDBY and rec["count"] == self.round:
                self.ready_rows.add(i)
        state = packets.decode_berry_game_state(packets.packet_bytes_of_slot(rows[0]))
        if state is not None and self.stage in ("play", "wait_end"):
            self.state = state
            ate = state["ate_berry"][self.mp_id]
            if ate and not self._prev_ate:
                self.ate_seen[-1] += 1
            self._prev_ate = ate
        all_blocks = len([i for i in self.blocks if i < n]) == n
        all_ready = all(i in self.ready_rows for i in range(n))
        if self.stage == "play" and self.state and self.state["num_gray_squares"] >= 10:
            self._advance("wait_end")
        elif self.stage == "wait_end" and self.state and self._ready_to_end():
            self._block_stage(bytes(bg.RESULTS_BLOCK_SIZE), "results")
        elif self.stage == "results" and all_blocks:
            for i in range(n):
                assert len(self.blocks[i]) >= bg.RESULTS_BLOCK_SIZE, (i, len(self.blocks[i]))
            self.result_blocks.append(self.blocks[0][:bg.RESULTS_BLOCK_SIZE])
            self._block_stage(bytes([bg.PLAY_AGAIN_NONE]), "results_ack")
        elif self.stage == "results_ack" and all_blocks:
            if self._highest_score() >= 3000:          # AskPlayAgain case 0 [:1232]
                self._save_left = 3
                self.saves += 1
                self._advance("save")
            else:
                self._block_stage(bytes([self.answers[0]]), "ask")
        elif self.stage == "save" and all_ready:
            self.ready_rows.clear()
            self.round += 1
            self._save_left -= 1
            if self._save_left == 0:
                self._block_stage(bytes([self.answers[0]]), "ask")
        elif self.stage == "ask" and all_blocks:
            self.leader_answers.append(self.blocks[0][0])
            answer = self.answers.pop(0)
            everyone = [self.blocks[i][0] for i in range(n)]
            if answer == bg.PLAY_AGAIN_NO or bg.PLAY_AGAIN_NO in everyone:
                self._advance("close")
            else:
                self._advance("again1")                  # ResetGame -> countdown [:930-955]
        elif self.stage in ("again1", "again2") and all_ready:
            self.ready_rows.clear()
            self.round += 1
            self.state = None
            self._advance("again2" if self.stage == "again1" else "ready_start")

    def _highest_score(self):
        block = self.result_blocks[-1]
        best = 0
        for p in range(self.n):
            r = [int.from_bytes(block[(p * bg.NUM_BERRY_IDS + k) * 2:][:2], "little")
                 for k in range(bg.NUM_BERRY_IDS)]
            got = r[0] * 10 + r[1] * 30 + r[2] * 50
            lost = r[3] * 50
            best = max(best, got - lost if got > lost else 0)
        return best

    def _ready_to_end(self):
        """ReadyToEndGame_Member [:2266-2280]"""
        s = self.state
        if not s["all_ready_to_end"]:
            return False
        start, end = bg.get_active_berry_columns(self.n)
        fall = s["fall_dist"] + [s["fall_dist"][0]]
        return all(fall[BT.ACTIVE_COLUMN_MAP[self.n - 1][self.mp_id][i]] == bg.MAX_FALL_DIST
                   for i in range(start, end))

    def next_cmd(self):
        if self.sender is not None and not self.sender.done:
            return rfu.serialize(self.sender.tick(None))
        if self.stage == "play" and self.state is not None:
            for head, col in enumerate(BT.DODRIO_HEAD_TO_COLUMN_MAP[self.n - 1][self.mp_id]):
                if (self.state["pick_states"][self.mp_id] == bg.PICK_NONE
                        and self.state["fall_dist"][col % 10] in (6, 7)
                        and self.rng.random() < self.skill):
                    return rfu.serialize(packets.send_words(
                        packets.encode_berry_pick_state(bg.HEAD_TO_PICK[head])))
            return rfu.idle_slot()
        if self.stage == "wait_end":
            if (self.state is not None and not self.state["berries_falling"]
                    and not self.state["all_ready_to_end"]):
                return rfu.serialize(packets.send_words(packets.encode_berry_ready_end(True)))
            return rfu.idle_slot()
        if self.stage in ("save", "again1", "again2"):
            return rfu.serialize(rfu.exit_standby_words(self.round))
        if self.stage == "close":
            return rfu.serialize(rfu.close_link_words(self.round))
        if self.stage in ("results", "results_ack", "ask"):
            return rfu.idle_slot()
        return super().next_cmd()


def _decode_results(block):
    return [[int.from_bytes(block[(p * bg.NUM_BERRY_IDS + k) * 2:][:2], "little")
             for k in range(bg.NUM_BERRY_IDS)] for p in range(5)]


def _run_dodrio(num_players, skill, answers, seed, frames=60 * 60 * 40):
    engine = HostBerryPickEngine(link_player=linkplayer.LinkPlayer(name="LEADER"),
                                 num_players=num_players, rng=random.Random(seed))
    console = PlayingMemberConsole(linkplayer.LinkPlayer(name="SWITCH"), num_players,
                                   skill=skill, answers=answers, seed=seed + 1)
    child_cmd = rfu.idle_slot()
    for _ in range(frames):
        table = [rfu.serialize(engine.tick()), child_cmd] + engine.extra_rows()
        console.feed_table(table)
        child_cmd = console.next_cmd()
        engine.feed_child_slot(child_cmd)
        if engine.disconnect_requested:
            break
    return engine, console


def test_dodrio_whole_game_record_is_what_the_console_saw_itself_eat():
    engine, console = _run_dodrio(3, skill=0.08, answers=[bg.PLAY_AGAIN_NO], seed=5)
    assert engine.games_played == 1, (engine.state, console.stage)
    picked, _scores = engine.results[0]
    results = _decode_results(console.result_blocks[0])
    # the whole 60-byte block, every player: picks and misses as the leader scored them
    assert [sum(r[:3]) for r in results[:3]] == picked
    assert results[1][bg.BERRY_MISSED] >= 1
    assert sum(results[1][:3]) > 0
    # The console's ateBerry edges can merge two quick eats, so its own count may trail by a few.
    assert 0.95 * picked[1] <= console.ate_seen[0] <= picked[1]
    # the leader answered (YES is the leader's own answer); then the link closed on request
    assert console.leader_answers == [bg.PLAY_AGAIN_YES]
    assert console.closed and engine.disconnect_requested


def test_dodrio_save_then_play_again_then_quit():
    engine, console = _run_dodrio(3, skill=0.3, answers=[bg.PLAY_AGAIN_YES, bg.PLAY_AGAIN_NO],
                                  seed=8)
    assert engine.games_played == 2, (engine.state, console.stage, console.history[-5:])
    assert console.saves >= 1                      # a best score of 3000+ ran the link save
    for game, block in enumerate(console.result_blocks):
        assert [sum(r[:3]) for r in _decode_results(block)[:3]] == engine.results[game][0]
    assert len(engine.standby_rounds) == 4 + 3 * console.saves + 2
    assert console.closed and engine.disconnect_requested


# --- review round 2: paths the first tests did not reach ---------------------------------------

import os  # noqa: E402
import re  # noqa: E402

DECOMP = _decomp_src() or ""


@pytest.mark.skipif(not os.path.isdir(DECOMP), reason="needs the pokefirered checkout")
def test_play_again_and_function_constants_match_the_decomp():
    jump = open(os.path.join(DECOMP, "pokemon_jump.c"), encoding="utf-8").read()
    assert int(re.search(r"#define PLAY_AGAIN_NO\s+(\d+)", jump).group(1)) == pj.PLAY_AGAIN_NO
    assert int(re.search(r"#define PLAY_AGAIN_YES\s+(\d+)", jump).group(1)) == pj.PLAY_AGAIN_YES
    funcs = re.search(r"enum \{\s*(FUNC_GAME_INTRO.*?)\};", jump, re.S).group(1)
    names = [n.strip().rstrip(",") for n in funcs.split("\n") if n.strip().startswith("FUNC_")]
    assert [getattr(pj, n) for n in names] == list(range(len(names)))
    berry = open(os.path.join(DECOMP, "dodrio_berry_picking.c"), encoding="utf-8").read()
    block = re.search(r"enum \{\s*(PLAY_AGAIN_NONE.*?)\};", berry, re.S).group(1)
    assert "PLAY_AGAIN_NONE,\n    PLAY_AGAIN_YES,\n    PLAY_AGAIN_NO," in block.replace("\r", "")
    assert (bg.PLAY_AGAIN_NONE, bg.PLAY_AGAIN_YES, bg.PLAY_AGAIN_NO) == (0, 1, 2)


class SlowLeaderModel:
    """A Pokemon Jump leader that answers a member's standby only `delay` frames after it is
    ready (the Switch walks through EnterMinigame first), then drives RESET_GAME -> GAME_INTRO
    (seed) -> one GAME_ROUND where the leader is hit -> GAME_OVER -> SAVE with `save_gap` frames
    between its three standby answers -> ASK_PLAY_AGAIN -> EXIT."""

    def __init__(self, delay, save_gap, seed=0x4242, drop_first_ready=False):
        self.inner = LeaderConsoleModel(linkplayer.LinkPlayer(name="SWITCH"))
        self.drop_ready = drop_first_ready     # lose the member's first READY of each round
        self.delay, self.save_gap, self.seed = delay, save_gap, seed
        self.round = 0
        self.member_ready_at = None
        self.func = None
        self.member = {}
        self.history = []
        self.answered_at = None
        self.play_again_seen = None
        self.leader_hit = False
        self.frames = 0

    def feed_member(self, slot):
        rec = rfu.parse_slot(slot)
        if rec and rec["op"] == rfu.READY_EXIT_STANDBY and self.drop_ready \
                and self.member_ready_at is None and getattr(self, "_dropped", None) != self.round:
            self._dropped = self.round           # this one never arrives; only a re-send can
            return
        if self.inner.stage != "game" or self.func is None:
            if rec and rec["op"] == rfu.READY_EXIT_STANDBY and rec["count"] == self.round \
                    and self.member_ready_at is None:
                self.member_ready_at = self.frames
            self.inner.feed_member(slot)
            return
        if rec and rec["op"] == rfu.READY_EXIT_STANDBY and rec["count"] == self.round \
                and self.member_ready_at is None:
            self.member_ready_at = self.frames
        if rec and rec["op"] == rfu.SEND_PACKET:
            ms = packets.decode_jump_member_state(packets.bytes_from_words(rec["packet"]))
            if ms is not None:
                self.member = ms

    def _standby_answer(self, gap):
        if self.member_ready_at is not None and self.frames - self.member_ready_at >= gap:
            words = rfu.exit_standby_words(self.round)
            self.round += 1
            self.member_ready_at = None
            return rfu.serialize(words)
        return None

    def _go(self, func):
        self.func = func
        self.history.append(pj.FUNC_NAMES[func])

    def row0(self):
        self.frames += 1
        inner = self.inner
        if inner.stage in ("exchange",) or inner.queue:
            return inner.row0()
        if inner.stage == "standby":
            answer = self._standby_answer(self.delay)
            if answer is not None:
                inner.stage = "mon_info"
                return answer
            return rfu.idle_slot()
        if inner.stage == "mon_info":
            if inner.member_mon is not None:
                inner.stage = "game"
                self._go(pj.FUNC_RESET_GAME)
            return rfu.serialize(packets.send_words(packets.encode_jump_mon_info(25, 1, 2)))
        m = self.member
        done = m.get("func_finished") and m.get("func_id") == self.func
        if done:
            if self.func == pj.FUNC_RESET_GAME:
                self._go(pj.FUNC_GAME_INTRO)
            elif self.func == pj.FUNC_GAME_INTRO:
                self._go(pj.FUNC_WAIT_ROUND)
            elif self.func == pj.FUNC_WAIT_ROUND:
                self._go(pj.FUNC_GAME_ROUND)
            elif self.func == pj.FUNC_GAME_ROUND:
                self.leader_hit = True           # the Switch player missed this swing
                self._go(pj.FUNC_GAME_OVER)
            elif self.func == pj.FUNC_GAME_OVER and m.get("jump_state"):
                self._go(pj.FUNC_SAVE)
            elif self.func == pj.FUNC_SAVE:
                self._go(pj.FUNC_ASK_PLAY_AGAIN)
            elif self.func == pj.FUNC_ASK_PLAY_AGAIN:
                self.play_again_seen = m.get("play_again_state")
                self._go(pj.FUNC_EXIT)
        if self.func == pj.FUNC_SAVE:
            answer = self._standby_answer(self.save_gap)
            if answer is not None:
                return answer
        data = {pj.FUNC_GAME_INTRO: self.seed}.get(self.func, 0)
        mon_state = packets.MONSTATE_HIT if self.leader_hit else 0
        jump_state = packets.JUMPSTATE_FAILURE if self.leader_hit else 0
        return rfu.serialize(packets.send_words(packets.encode_jump_leader_state(
            func_id=self.func, mon_state=mon_state, received_bonus_flags=0,
            jump_state=jump_state, jump_time_start=0, data=data, jumps_in_row=0,
            jump_score=0)))


@pytest.mark.parametrize("delay,save_gap,drop", [(400, 150, False), (130, 300, False),
                                                 (5, 10, False), (200, 200, True)])
def test_pokejump_member_survives_slow_standbys_game_over_and_the_link_save(delay, save_gap, drop):
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"), trust_pia=True)
    leader = SlowLeaderModel(delay, save_gap, drop_first_ready=drop)
    member_slot = rfu.idle_slot()
    for _ in range(20000):
        member.feed_in_frame({"positional": [(0, leader.row0()), (1, member_slot)]})
        member_slot = rfu.serialize(member.tick())
        leader.feed_member(member_slot)
        if leader.func == pj.FUNC_EXIT and member.barrier.mode == "CLOSE":
            break
    assert leader.history == ["RESET_GAME", "GAME_INTRO", "WAIT_ROUND", "GAME_ROUND",
                              "GAME_OVER", "SAVE", "ASK_PLAY_AGAIN", "EXIT"], leader.history
    assert member.standby_rounds == [0, 1, 2, 3]       # entry + three save rounds
    assert leader.play_again_seen == pj.PLAY_AGAIN_YES
    assert member.barrier.mode == "CLOSE"              # READY_CLOSE_LINK after EXIT
    assert member.hits == 0                            # the leader's hit ended the game


def test_pokejump_intro_takes_the_leaders_seed():
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"))
    member.mpid, member.player_count, member.state = 1, 2, pj.PLAYING
    member.leader_state = {"data": 0xBEEF, "func_id": pj.FUNC_GAME_INTRO, "mon_state": 0,
                           "jump_state": 0}
    member._set_func(pj.FUNC_GAME_INTRO)
    assert member.model.rng_seed == 0xBEEF


import copy  # noqa: E402


def test_berry_bots_never_take_a_berry_the_switch_player_can_still_take():
    """Play whole games with the Switch player idle on random frames; every bot pick that
    lands on a column the Switch player reaches must be a virtual member's, on the last frame a
    pick can land, while the Switch player is not attempting it."""
    for n in (3, 4, 5):
        g = bg.DodrioLeaderGame(n, rng=random.Random(n))
        g.init_first_wave()
        bots = [bg.BerryBot(g, i, lag=(5 if i == 0 else 1)) for i in range(n) if i != 1]
        rng = random.Random(100 + n)
        switch_cols = set(BT.DODRIO_HEAD_TO_COLUMN_MAP[n - 1][1])
        sent, checked = None, 0
        for _ in range(60 * 60 * 2):
            if g.func != bg.FUNC_PLAY_GAME:
                break
            picks, leader_pick = {}, bg.PICK_NONE
            for bot in bots:
                choice = bot.choose()
                if choice == bg.PICK_NONE:
                    continue
                head = {bg.PICK_LEFT: 0, bg.PICK_MIDDLE: 1, bg.PICK_RIGHT: 2}[choice]
                col = BT.DODRIO_HEAD_TO_COLUMN_MAP[n - 1][bot.player_id][head]
                if col in switch_cols:
                    checked += 1
                    assert bot.player_id != 0
                    assert g.input_state[1] not in (bg.INPUTSTATE_TRY_PICK, bg.INPUTSTATE_PICKED)
                    # the counterexample check: without this bot, could the Switch player's
                    # press on any of the next frames still have taken the berry?
                    for wait in range(1, 13):
                        alt = copy.deepcopy(g)
                        for _ in range(wait):
                            alt.leader_frame(None, {}, bg.PICK_NONE)
                        if alt.func != bg.FUNC_PLAY_GAME or alt.input_state[1]:
                            continue
                        heads = BT.DODRIO_HEAD_TO_COLUMN_MAP[n - 1][1]
                        alt.leader_frame(None, {1: bg.HEAD_TO_PICK[heads.index(col)]},
                                         bg.PICK_NONE)
                        assert 1 not in alt.players_attempting_pick[col], (n, col, wait)
                if bot.player_id == 0:
                    leader_pick = choice
                else:
                    picks[bot.player_id] = choice
            if rng.random() < 0.03:         # the Switch player sometimes picks
                for head, col in enumerate(BT.DODRIO_HEAD_TO_COLUMN_MAP[n - 1][1]):
                    if g.fall_dist[col] in (6, 7) and g.berry_state[col] == bg.BERRYSTATE_NONE:
                        picks[1] = bg.HEAD_TO_PICK[head]
                        break
            sent = g.leader_frame(sent, picks, leader_pick, row0_is_packet=sent is not None)
        assert checked > 0, n            # the rule was exercised


def test_berry_leader_keeps_its_game_running_and_accepts_picks_across_held_frames():
    engine = HostBerryPickEngine(link_player=linkplayer.LinkPlayer(name="LEADER"), num_players=3,
                                 rng=random.Random(3))
    engine.state = "PLAY"
    engine.game.init_first_wave()
    for _ in range(30):
        engine.tick()
    frames = engine.game.frames
    for _ in range(10):
        engine.idle_frame()                 # the reliable window holds these rows back
    assert engine.game.frames == frames + 10
    assert engine._row0_was_packet
    # a pick arriving right after the held frames is read (row 0 still carries a GameState)
    g = engine.game
    for head, col in enumerate(BT.DODRIO_HEAD_TO_COLUMN_MAP[2][1]):
        g.fall_dist[col], g.berry_state[col], g.fall_timer[col] = 6, bg.BERRYSTATE_NONE, 0
    engine.feed_child_slot(rfu.serialize(packets.send_words(
        packets.encode_berry_pick_state(bg.PICK_MIDDLE))))
    engine.tick()
    assert g.input_state[1] != bg.INPUTSTATE_NONE


def test_berry_second_game_does_not_inherit_the_first_games_gray_squares():
    engine, console = _run_dodrio(3, skill=0.3, answers=[bg.PLAY_AGAIN_YES, bg.PLAY_AGAIN_NO],
                                  seed=21)
    assert engine.games_played == 2
    # each game lasted: the second did not end on its first frames
    assert all(sum(p) > 20 for p, _ in engine.results)


def test_pokejump_member_ends_its_round_when_another_player_is_hit_and_resets_jump_states():
    """UpdateVineHitStates returns FALSE when any player's monState is HIT [pokemon_jump.c
    :2451-2457], so GameRound_Member ends at once; WAIT_ROUND clears jump states
    (ResetPlayersJumpStates [:1338-1345]); ASK_PLAY_AGAIN stops the member's packets [:1486-1490]."""
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"))
    member.mpid, member.player_count, member.state = 1, 2, pj.PLAYING
    member.model.init_vine_state()
    member.leader_state = {"data": 7, "func_id": pj.FUNC_GAME_INTRO, "mon_state": 0,
                           "jump_state": 0}
    member._set_func(pj.FUNC_GAME_INTRO)
    for _ in range(pj.INTRO_FRAMES + 2):
        member.tick()
    member.model.jump_state = packets.JUMPSTATE_SUCCESS
    member._set_func(pj.FUNC_WAIT_ROUND)
    assert member.model.jump_state == packets.JUMPSTATE_NONE
    member.tick()
    member._set_func(pj.FUNC_GAME_ROUND)
    for _ in range(5):
        member.tick()
    assert member.func_active
    member.others[0] = {"mon_state": packets.MONSTATE_HIT, "jump_state": packets.JUMPSTATE_FAILURE}
    member.tick()
    assert not member.func_active and member.func_finished
    assert member.model.game_over
    member._set_func(pj.FUNC_ASK_PLAY_AGAIN)
    assert member.link_timer_limit is None
    assert all(member._member_state_words() is None for _ in range(8))
