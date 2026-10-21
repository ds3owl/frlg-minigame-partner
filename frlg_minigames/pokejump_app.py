"""Pokemon Jump with the Switch leading the group and the PC joining it as one member.

PokeJumpApp scans until the Switch's group appears, joins, and plays every round, the save and
play-again until `stop` (a threading.Event) is set or the leader closes the link; then it scans
again for the next group. `status(text)` is called on every milestone."""

import os
import threading
import time

from frlg_minigames import pokejump_member as pj
from frlg_minigames.net.frlg.link import beacon, sim as simmod
from frlg_minigames.net.host_support import resolve_keys
from frlg_minigames.net.ldn import crypto as cryptomod, pia_connect, transport as tmod
from frlg_minigames.profile import LdnConfig, TrainerProfile

FRAME_SECONDS = 1.0 / 59.727
RESCAN_SECONDS = 3.0


class PokeJumpApp:
    def __init__(self, *, profile=None, ldn=None, species=pj.SPECIES_CHARMANDER, trust_pia=False,
                 play_again=True, log=print, status=None, stop=None):
        self.profile = profile or TrainerProfile()
        self.ldn = ldn or LdnConfig()
        self.species = species
        self.trust_pia = trust_pia
        self.play_again = play_again
        self.log = log
        self.info = getattr(log, "info", log)
        self.status = status or self.info
        self.stop = stop or threading.Event()
        self.engine = None
        self.streak = 0
        self.connected_at = None

    def _keys(self):
        keys = resolve_keys(self.ldn.keys_path)
        if not os.path.exists(keys):
            raise FileNotFoundError(f"prod.keys not found at {keys!r}")
        return keys

    def run(self):
        """Scan, join, play; repeat for the next group until stopped. True once a game reached
        its first round."""
        keys = self._keys()
        reached = False
        scanning_said = False
        while not self.stop.is_set():
            if not scanning_said:
                self.status("Looking for your group. On the Switch: BECOME LEADER.")
                scanning_said = True
            try:
                t = tmod.LiveTransport(password=self.ldn.password, nickname=self.profile.name,
                                       keys_path=keys, local_comm_id=self.ldn.local_comm_id,
                                       phyname=self.ldn.phy, log=self.log,
                                       beacon_debug=beacon.diagnose).start(attempts=1)
            except RuntimeError as exc:
                if "no joinable" not in str(exc) and "timed out" not in str(exc):
                    raise
                self.stop.wait(RESCAN_SECONDS)
                continue
            scanning_said = False
            reached = self._play(t) or reached
        return reached

    def _play(self, t):
        pc = cryptomod.PiaCrypto(t.ssid)
        engine = self.engine = pj.PokeJumpMemberEngine(
            self.profile.to_link_player(), species=self.species, trust_pia=self.trust_pia,
            play_again=self.play_again, log=self.log)
        conn = pia_connect.ConnectionManager(
            our_mac=t.our_mac or b"\x00" * 6, host_mac=t.host_mac or b"\x00" * 6,
            our_ip=t.our_ip, host_ip=t.host_ip, player_name=self.profile.name,
            random4=os.urandom(4), log=self.log)
        connect_id = (int.from_bytes(os.urandom(2), "big") or 1).to_bytes(2, "big")
        s = simmod.Sim(t, pc, engine, t.our_ip, t.host_ip, conn=conn, connect_id=connect_id,
                       capture_path=self.ldn.capture_path, log=self.log)
        said = set()
        next_frame = time.monotonic()
        try:
            while not self.stop.is_set():
                s.tick()
                if getattr(s, "ni_rejected", False):
                    self.status("The group refused the PC (full or already started).")
                    break
                if getattr(s, "host_disconnected", False):
                    self.status("The Switch closed the group.")
                    break
                if s.connected and "conn" not in said:
                    said.add("conn")
                    self.connected_at = time.monotonic()
                    self.status(f"{self.profile.name} joined your group. Start the game on the Switch.")
                if engine.reached_round and "round" not in said:
                    said.add("round")
                    self.status("Jumping.")
                self.streak = (engine.leader_state or {}).get("jumps_in_row", 0)
                # Pace on absolute frame times: the bot's vine advances once per tick, like the
                # console's once per VBlank.
                next_frame += FRAME_SECONDS
                delay = next_frame - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.25:
                    next_frame = time.monotonic()      # fell far behind: do not burst
        finally:
            s.close()
            t.stop()
            self.info(f"Link closed. Swings the PC cleared: {engine.rounds_cleared}; PC hits: "
                      f"{engine.hits}; games: {engine.games}; states: {engine.state_history}")
        return engine.reached_round
