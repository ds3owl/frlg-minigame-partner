"""End to end against the original game: the PC's Dodrio Berry Picking leader on the link layer's host
stack, an in-memory LDN wire (Pia, reliable, RFU), and on the far side a member console built from
pret/pokefirered's own link and minigame code (tests/rig/console), played by button presses.

Skipped without gcc or a pokefirered checkout."""

import pytest

from tests.rig.console import build
from tests.rig.wire import Wire

LIB = None


def _lib():
    global LIB
    if LIB is None:
        LIB = build.build()
    return LIB


pytestmark = pytest.mark.skipif(build.decomp_root() is None, reason="needs pret/pokefirered")


def _session(num_players, answers, skill, *, delay=1, loss=0.0, seed=0):
    from frlg_minigames.profile import TrainerProfile
    from frlg_minigames.session import MinigameHostSession
    from frlg_minigames.berrypick_app import build_berry_pick_app_data
    from frlg_minigames.berrypick_host import HostBerryPickEngine
    from tests.rig.console import console as fc
    import random
    profile = TrainerProfile(name="LEADER", version="firered")
    engine = HostBerryPickEngine(link_player=profile.to_link_player(), num_players=num_players,
                                 rng=random.Random(seed))
    session = MinigameHostSession(engine=engine)
    _inactive, active = build_berry_pick_app_data(profile, session.rfu.host_session_id)
    player = fc.BerryPlayer(num_players, skill=skill, answers=answers, seed=seed)
    console = fc.MemberConsole(_lib(), activity=11, player=player)
    wire = Wire(session, profile, active, console, joiner_name="SWITCH", delay=delay,
                loss=loss, seed=seed)
    return engine, console, wire


def _play(engine, console, wire, frames):
    wire.run(frames, until=lambda: engine.disconnect_requested)
    return engine.disconnect_requested


@pytest.mark.parametrize("num_players,delay,loss", [(3, 1, 0.0), (5, 1, 0.0), (3, 3, 0.02)])
def test_original_console_plays_a_whole_game_and_saves_the_leaders_count(num_players, delay,
                                                                        loss):
    engine, console, wire = _session(num_players, answers=[2], skill=0.2, delay=delay,
                                     loss=loss, seed=num_players)
    assert _play(engine, console, wire, 60 * 60 * 6), (engine.state, engine.state_history)
    # the console accepted every virtual member as a player
    names = console.link_player_names(num_players)
    assert names[:2] == ["LEADER", "SWITCH"]
    assert all(n.startswith("DODRIO") for n in names[2:])
    assert console.lib.fc_player_count() == num_players
    # its own record [TryUpdateRecords] is what the leader counted for it
    assert engine.games_played == 1
    picked, scores = engine.results[0]
    records = console.records()
    assert records["berries_picked"] == picked[1] > 0
    assert records["best_score"] == min(scores[1], 999990)
    # five-player games count berries in a row [UpdateBerriesPickedInRow :2542-2563]
    assert records["berries_in_row"] == engine.results_in_row[0]
    if num_players == 5:
        assert records["berries_in_row"] > 0
    assert engine.state == "CLOSE"


def test_original_console_saves_at_3000_plays_again_then_quits():
    engine, console, wire = _session(3, answers=[1, 2], skill=0.6, seed=7)
    assert _play(engine, console, wire, 60 * 60 * 20), (engine.state, engine.state_history)
    assert engine.games_played == 2
    best = max(max(scores) for _picked, scores in engine.results)
    assert best >= 3000
    assert console.saves_done >= 1                 # Task_LinkFullSave's three rounds completed
    records = console.records()
    assert records["berries_picked"] == max(p[1] for p, _s in engine.results)
    assert records["best_score"] == max(s[1] for _p, s in engine.results)


# --- Pokemon Jump: the original game leads, the PC's member bot joins ------------------------

def _jump_session(streak, *, species=4, answers=(1,), delay=1, loss=0.0, seed=0):
    from frlg_minigames.profile import TrainerProfile
    from frlg_minigames.net.frlg.link import linkplayer
    from frlg_minigames.session import MinigameHostSession
    from frlg_minigames.net.frlg.link.host_beacon import _build_activity_app_data
    from frlg_minigames import pokejump_member as pj
    from tests.rig.console import console as fc
    profile = TrainerProfile(name="SWITCH", version="firered")
    player = fc.JumpLeaderPlayer(streak=streak, answers=list(answers))
    leader = fc.LeaderConsole(_lib(), activity=9, player=player, species=species)
    session = MinigameHostSession(engine=leader)
    _inactive, active = _build_activity_app_data(profile, session.rfu.host_session_id, 9)
    member = pj.PokeJumpMemberEngine(linkplayer.LinkPlayer(name="BOT"))
    wire = Wire(session, profile, active, member, joiner_name="BOT", delay=delay, loss=loss,
                seed=seed)
    return leader, member, player, wire


@pytest.fixture
def switch_transport(monkeypatch):
    """Here the link layer's host stack stands in for the Switch's own transport. Its hole guard holds
    new rows while 6 are unacknowledged (tuned for a console's ~1-frame round trip), which on a
    slow wire starves a leader that, like any GBA parent, sends every VBlank. The Switch has no
    such guard; widen it so the stand-in carries every frame."""
    from frlg_minigames.net.frlg.link import host_session
    monkeypatch.setattr(host_session, "HOST_OUTSTANDING_MAX", 64)


@pytest.mark.parametrize("delay,loss", [(1, 0.0), (3, 0.02)])
def test_original_leader_and_the_member_bot_play_a_game_to_the_end(delay, loss,
                                                                   switch_transport):
    leader, member, player, wire = _jump_session(30, delay=delay, loss=loss, seed=delay)
    assert wire.run(60 * 60 * 5, until=lambda: leader.exited), leader.jump_state()[:2]
    assert leader.link_player_names(2) == ["SWITCH", "BOT"]
    assert member.hits == 0                       # the bot was never the one hit
    assert player.max_streak >= 30
    assert leader.record_jumps_in_row() == player.max_streak   # the leader's own saved record


def test_original_leader_reaches_200_in_a_row_with_the_bot_and_saves(switch_transport):
    leader, member, player, wire = _jump_session(205)
    assert wire.run(60 * 60 * 8, until=lambda: leader.exited), leader.jump_state()[:2]
    assert member.hits == 0
    assert leader.record_jumps_in_row() >= 200    # the trainer card's Pokemon Jump condition
    assert leader.saves_done == 1                 # jumpsInRow >= 200 runs the link save
    assert member.standby_rounds == [0, 1, 2, 3]  # entry + the save's three rounds
