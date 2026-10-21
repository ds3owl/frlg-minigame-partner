#!/usr/bin/env python3
"""Replay a Pokemon Jump member capture (the app's run logs keep one per run) offline and report
what the leader's state packets said: the streak (jumpsInRow), the score, the function sequence.

    .venv\\Scripts\\python tools\\jump_capture_stats.py RUN-jump.jsonl [--verbose]

The host IN stream is decrypted with the capture's SSID and fed through the RX stack
(frlg_minigames.net.frlg.link.sim.Sim) into a PokeJumpMemberEngine. Every leader row is decoded
regardless of the engine's state, so the numbers are the leader's, not the bot's bookkeeping.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import frlg_minigames  # noqa: E402,F401  (the bundled LDN library on sys.path)

from frlg_minigames.net.ldn import crypto as cryptomod, transport as tmod  # noqa: E402
from frlg_minigames.net.frlg.link import sim as simmod  # noqa: E402
from frlg_minigames import packets, pokejump_member as pj  # noqa: E402

FUNC_NAMES = pj.FUNC_NAMES          # the leader's funcId [pokemon_jump.c enum]


def collapse_funcs(funcs):
    """['WAIT_ROUND','GAME_ROUND'] x N -> 'N rounds'; everything else verbatim."""
    out, i = [], 0
    while i < len(funcs):
        n = 0
        while i + 1 < len(funcs) and funcs[i] == pj.FUNC_WAIT_ROUND and funcs[i + 1] == pj.FUNC_GAME_ROUND:
            n += 1
            i += 2
        if n:
            out.append(f"{n} rounds")
        else:
            out.append(FUNC_NAMES.get(funcs[i], str(funcs[i])))
            i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    t = tmod.ReplayTransport.from_capture(args.capture)
    if not t.ssid:
        sys.exit("capture has no SSID - cannot decrypt")
    lg = print if args.verbose else (lambda *a: None)
    pc = cryptomod.PiaCrypto(t.ssid)
    engine = pj.PokeJumpMemberEngine(None, log=lg)
    s = simmod.Sim(t, pc, engine, t.our_ip, t.host_ip, log=lg)

    samples = []            # (rx_index, leader_state)
    orig = engine.feed_in_frame

    def hooked(unwrapped):
        if unwrapped is not None:
            rows = dict(unwrapped.get("positional", []))
            slot = rows.get(0)
            if slot is not None:
                st = packets.decode_jump_leader_state(packets.packet_bytes_of_slot(slot))
                if st is not None:
                    samples.append((s.rx_count, st))
        return orig(unwrapped)

    engine.feed_in_frame = hooked
    while not t.drained:
        s.tick()
    print(f"[replay] processed {s.rx_count} IN datagrams; leader state packets: {len(samples)}")
    print(f"[replay] engine states: {engine.state_history}")
    print(f"[replay] leader announced players={engine.player_count} our mpid={engine.mpid} "
          f"leader={getattr(engine.leader_link_player, 'name', None)!r}")
    if not samples:
        print("No leader state packets in this capture (the game never reached PLAY_GAME).")
        return 1

    # Streaks: jumpsInRow climbs by one per cleared swing and falls to 0 on a hit or a new game.
    best = 0
    best_score = 0
    streaks = []
    prev = None
    funcs = []
    for _, st in samples:
        j, sc = st["jumps_in_row"], st["jump_score"]
        best, best_score = max(best, j), max(best_score, sc)
        if prev is not None and j < prev and prev > 0:
            streaks.append(prev)
        prev = j
        if not funcs or funcs[-1] != st["func_id"]:
            funcs.append(st["func_id"])
    if prev:
        streaks.append(prev)
    last = samples[-1][1]
    print(f"Jumps in a row (best this session): {best}")
    print(f"Jump score (best this session):     {best_score}")
    print(f"Streaks, in order (ended by a hit or the end): {streaks}")
    print(f"Games (GAME_INTRO seen): {funcs.count(pj.FUNC_GAME_INTRO)}; leader function sequence: "
          f"{collapse_funcs(funcs)}")
    print(f"Bot (replayed): swings cleared {engine.rounds_cleared}, hits {engine.hits}, games {engine.games}")
    who = ("the Switch player (leader jump_state=FAILURE)" if last["jump_state"] == packets.JUMPSTATE_FAILURE
           else "not the leader (its last jump_state is not FAILURE)")
    print(f"Who ended the last game: {who}; bot hits in replay: {engine.hits}")
    print(f"Last leader packet: {last}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
