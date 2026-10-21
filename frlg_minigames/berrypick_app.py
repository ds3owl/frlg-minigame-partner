"""Dodrio Berry Picking with the PC leading the group: the Switch joins it as one member.

BerryPickApp owns the OS resources and the loop; HostBerryPickEngine (berrypick_host.py) is the
game. `status(text)` is called on every milestone and `stop` (a threading.Event) ends the run."""

import os
import threading
import time

from frlg_minigames.berrypick_host import (
    BP_ASK, BP_CLOSE, BP_PLAY, BP_PREGAME, BP_RESULTS, BP_WAIT_END, HostBerryPickEngine)
from frlg_minigames.net.frlg.link.host_beacon import (
    BeaconInjector, NullBeaconInjector, _build_activity_app_data)
from frlg_minigames.net.frlg.link.linkplayer import HOST_NAME_PAD
from frlg_minigames.net.host_support import resolve_keys
from frlg_minigames.net.ldn import ldntrace, transport
from frlg_minigames.net.ldn.host_pia import HostPeerProtocol
from frlg_minigames.profile import HostOptions, LdnConfig, TrainerProfile
from frlg_minigames.session import MinigameHostSession

ACTIVITY_BERRY_PICK = 11   # include/constants/union_room.h

POLL_SECONDS = 0.05
# Settle after the console leaves LDN following a confirmed room exit.
CLOSE_SETTLE_SECONDS = 2.0

STATE_MESSAGES = {
    BP_PREGAME: "Player exchange done; waiting for the console's standby and Dodrio info.",
    BP_PLAY: "Berries are falling.",
    BP_WAIT_END: "Ten misses: the game is over; waiting for every player.",
    BP_RESULTS: "Results sent.",
    BP_ASK: "Waiting for the Switch player's play-again answer.",
    BP_CLOSE: "The console is closing the link.",
}


def build_berry_pick_app_data(profile, host_session_id):
    """The group leader's advertisement: activity ACTIVITY_BERRY_PICK, nothing started. The
    console keeps a candidate only if the activity is in sAcceptedActivityIds_BerryPicking
    [src/data/union_room.h:404, 449]."""
    return _build_activity_app_data(profile, host_session_id, ACTIVITY_BERRY_PICK)


class BerryPickApp:
    def __init__(self, *, profile=None, ldn=None, options=None, num_players=3, trust_pia=True,
                 log=print, status=None, stop=None, transport_factory=transport.HostTransport):
        self.profile = profile or TrainerProfile()
        self.ldn = ldn or LdnConfig()
        self.options = options or HostOptions()
        self.num_players = num_players
        self.trust_pia = trust_pia
        self.log = log
        self.info = getattr(log, "info", log)
        self.status = status or self.info
        self.stop = stop or threading.Event()
        self.transport_factory = transport_factory
        self.engine = self.session = self.network = self.peer = self.injector = self.tracer = None
        self.joined = False
        self._last_state = None

    def _keys(self):
        keys = resolve_keys(self.ldn.keys_path)
        if not os.path.exists(keys):
            raise FileNotFoundError(f"prod.keys not found at {keys!r}")
        return keys

    def _build(self):
        keys = self._keys()
        link_player = self.profile.to_link_player()
        self.engine = HostBerryPickEngine(link_player=link_player, num_players=self.num_players,
                                          trust_pia=self.trust_pia, log=self.log)
        self.session = MinigameHostSession(engine=self.engine, log=self.log)
        inactive, active = build_berry_pick_app_data(self.profile, self.session.rfu.host_session_id)
        self.tracer = (ldntrace.Tracer(self.ldn.capture_path, log=self.log)
                       if self.ldn.capture_path else None)
        o = self.options
        # The board names itself as the phy ("esp32"), as `--phy auto` resolved it on hardware.
        phy = transport.find_ap_phy(log=self.log) if transport.board_radio() else self.ldn.phy
        self.network = self.transport_factory(
            app_data=inactive, password=self.ldn.password, nickname=self.profile.discovery_name,
            keys_path=keys, local_comm_id=self.ldn.local_comm_id, scene_id=o.scene_id,
            max_participants=o.max_participants, phyname=phy, channel=o.channel,
            skip_encryption=o.skip_encryption, accept_decrypted_ccmp=o.accept_decrypted_ccmp,
            tracer=self.tracer, log=self.log)
        self.peer = HostPeerProtocol(
            self.network, self.profile, self.session, active,
            native_nonce_sequence=o.native_nonce_sequence,
            session_response_first=o.session_response_first,
            protocol_tick_seconds=1.0 / o.protocol_tick_hz, tracer=self.tracer, log=self.log)
        wire = link_player.pack(name_pad=HOST_NAME_PAD)
        self.info(f"Leader identity: OT={self.profile.name!r}, TID=0x{self.profile.tid:04x}; "
                  f"name_bytes={wire[8:16].hex()}; players {self.num_players} "
                  f"({', '.join(v.name for v in self.engine.virtuals)} virtual).")

    def _progress(self):
        state = self.engine.state
        if state != self._last_state:
            self._last_state = state
            if state in STATE_MESSAGES:
                self.status(STATE_MESSAGES[state])

    def run(self):
        """True once the Switch joined. Ends on stop, on the console leaving, or on the close."""
        absent_since = None
        try:
            self._build()
            self.network.start(preflight=not self.options.skip_preflight)
            factory = BeaconInjector
            # The ESP32 access point beacons itself.
            if not getattr(self.transport_factory, "NEEDS_RADIO", True) or transport.board_radio():
                factory = NullBeaconInjector
            self.injector = factory(channel=self.options.channel, log=self.log)
            self.injector.start()
            self.status("Group open. On the Switch: JOIN GROUP and choose "
                        f"{self.profile.name}.")
            ni_logged = False
            while not self.stop.is_set():
                if self.injector.error is not None:
                    raise RuntimeError(f"beacon injector stopped: {self.injector.error}")
                if self.network.participants and not self.joined:
                    self.joined = True
                    self.peer.on_participant_joined()
                    self.status("The Switch joined the group.")
                if self.joined and not self.network.participants:
                    if not self.engine.close_confirmed or self.engine.done:
                        self.session.on_ldn_leave()
                        self.status("The Switch left the group.")
                        break
                    now = time.monotonic()
                    absent_since = absent_since or now
                    if now - absent_since >= CLOSE_SETTLE_SECONDS:
                        self.status("Dodrio Berry Picking session finished.")
                        break
                for datagram, src_ip in self.network.recv():
                    self.peer.receive(datagram, src_ip)
                    for out in self.peer.drain():
                        self.network.send(out.data, out.destination)
                now = time.monotonic()
                for out in self.peer.tick(now):
                    self.network.send(out.data, out.destination)
                self._progress()
                if self.session.rfu.ni_complete and not ni_logged:
                    ni_logged = True
                    self.info("RFU link up; starting the player exchange.")
                if self.engine.done and not self.network.participants:
                    self.status("Dodrio Berry Picking session finished.")
                    break
                self.network.wait_readable(self.peer.next_deadline(now, POLL_SECONDS))
        finally:
            if self.injector is not None:
                self.injector.stop()
            if self.network is not None:
                self.network.stop()
            if self.tracer is not None:
                self.tracer.close()
        return self.joined
