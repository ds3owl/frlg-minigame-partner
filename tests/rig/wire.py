"""An in-memory LDN wire between the link layer's host stack (HostPeerProtocol + HostSession) and its
joiner stack (frlg.link.sim.Sim + pia_connect.ConnectionManager), on a virtual clock, with
optional delay and loss. Everything above the radio runs for real: Pia crypto and handshake,
the reliable layer, RFU framing (C/A/NI/UNI), the parent's echo row, the activities."""

import os
import random
from collections import deque

from frlg_minigames.net.frlg.link import sim as simmod
from frlg_minigames.net.ldn import crypto as cryptomod, host_pia, pia_connect

HOST_IP = "169.254.7.1"
JOIN_IP = "169.254.7.2"
BROADCAST = "169.254.7.255"
HOST_MAC = bytes.fromhex("02000a000001")
JOIN_MAC = bytes.fromhex("02000a000002")


class _HostNetwork:
    def __init__(self, ssid, app_data):
        self.ssid = ssid
        self.our_ip = HOST_IP
        self.host_ip = HOST_IP
        self.our_mac = HOST_MAC
        self.broadcast = BROADCAST
        self.participants = [(JOIN_MAC, JOIN_IP)]
        self.max_participants = 8
        self.SCENE_ID = 0
        self.app_data = app_data

    def set_application_data(self, data):
        self.app_data = data


class _JoinTransport:
    def __init__(self, ssid, inbox):
        self.ssid = ssid
        self.our_ip, self.host_ip = JOIN_IP, HOST_IP
        self.our_mac, self.host_mac = JOIN_MAC, HOST_MAC
        self.inbox = inbox
        self.outbox = []

    def recv(self):
        out = list(self.inbox)
        self.inbox.clear()
        return out

    def send(self, datagram, dst_ip):
        self.outbox.append((bytes(datagram), dst_ip))


class Wire:
    """step() advances one VBlank on both sides. delay: frames each datagram spends on the wire
    (each direction); loss: probability a datagram is dropped."""

    # a hair over the host stack's protocol period, so float rounding never skips its tick
    VBLANK = (1.0 / 59.727) * 1.000001

    def __init__(self, host_session, host_profile, active_app_data, joiner_engine, *,
                 joiner_name="JOINER", delay=1, loss=0.0, seed=0, log=lambda *a: None):
        self.rng = random.Random(seed)
        self.delay, self.loss = delay, loss
        ssid = os.urandom(16)
        self.net = _HostNetwork(ssid, active_app_data)
        self.now = 1000.0
        host_pia.time.monotonic = lambda: self.now          # the host's one wall-clock read
        self.peer = host_pia.HostPeerProtocol(self.net, host_profile, host_session,
                                              active_app_data, log=log)
        self.session = host_session
        self._to_join = deque()
        self._to_host = deque()
        self._join_inbox = []
        self.t = _JoinTransport(ssid, self._join_inbox)
        conn = pia_connect.ConnectionManager(
            our_mac=JOIN_MAC, host_mac=HOST_MAC, our_ip=JOIN_IP, host_ip=HOST_IP,
            player_name=joiner_name, random4=os.urandom(4), log=log)
        self.sim = simmod.Sim(self.t, cryptomod.PiaCrypto(ssid), joiner_engine, JOIN_IP,
                              HOST_IP, conn=conn, connect_id=b"\x12\x34", log=log)
        self.engine = joiner_engine
        self.frame = 0
        self.dropped = 0
        self.peer.on_participant_joined()

    def _carry(self, queue, items):
        for item in items:
            if self.loss and self.rng.random() < self.loss:
                self.dropped += 1
                continue
            queue.append((self.frame + self.delay, item))

    def _arrived(self, queue):
        out = []
        while queue and queue[0][0] <= self.frame:
            out.append(queue.popleft()[1])
        return out

    def step(self):
        self.frame += 1
        self.now += self.VBLANK
        for data, _dst in self._arrived(self._to_host):
            self.peer.receive(data, JOIN_IP, now=self.now)
        self._carry(self._to_join, [o.data for o in self.peer.drain()])
        self._carry(self._to_join, [o.data for o in self.peer.tick(self.now)])
        for data in self._arrived(self._to_join):
            self._join_inbox.append((data, HOST_IP))
        # Both stacks wait for the other side's first RTT probe, which a real Switch sends
        # (each was built against consoles). Once the Session is finalized on both sides, the
        # wire declares the joiner live, as a host RTT would.
        conn = self.sim.conn
        if (conn.state == pia_connect.ST_FINALIZE and self.peer.session_finalized):
            conn.state = pia_connect.ST_CONNECTED
        self.sim.tick()
        self._carry(self._to_host, self.t.outbox)
        self.t.outbox = []

    def run(self, frames, until=None):
        for _ in range(frames):
            self.step()
            if until is not None and until():
                return True
        return until is None
