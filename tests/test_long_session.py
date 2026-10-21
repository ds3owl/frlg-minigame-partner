"""A Switch-led group must stay up past 11 minutes.

The joiner numbered its Pia packets per destination variable, the property acks to destination
0 included, while the host checks every destination other than 1 against one counter and accepts
an id only if (int16)(id - last) >= 1; id 0 passes and stores nothing (docs: Pia v5 receiver).
At ~50 data packets a second the data ids led the ack ids by 32768 after ~11 minutes; the next ack
then passed, set the host's counter low, and every data packet after it read as stale."""

from types import SimpleNamespace

from frlg_minigames.net.frlg.link.sim import Sim
from frlg_minigames.net.ldn import pia_connect

HOST_VAR = 0x7620
DATA_PER_SECOND = 50
ACK_EVERY = 25            # a property update about twice a second


def _manager():
    return pia_connect.ConnectionManager(our_mac=b"\x02" * 6, host_mac=b"\x04" * 6,
                                         our_ip="169.254.21.2", host_ip="169.254.21.1",
                                         player_name="Ash")


def _property_update(seqid):
    return bytes([0x01, pia_connect.NET_UPDATE_PROPERTY, 0x00, 0x00]) + seqid.to_bytes(4, "big")


class HostCounters:
    """The host's receive check for one station: destination 1, and every other destination."""

    def __init__(self):
        self.last = {}
        self.dropped = 0

    def accept(self, dst, pktid):
        if pktid == 0:
            return True
        bucket = "one" if dst == 1 else "other"
        last = self.last.get(bucket)
        if last is not None and ((pktid - last + 0x8000) & 0xFFFF) - 0x8000 < 1:
            self.dropped += 1
            return False
        self.last[bucket] = pktid
        return True


def test_property_acks_are_unsequenced():
    cm = _manager()
    cm.on_message(pia_connect.PROTO_NET, _property_update(7))
    (ack,) = [e for e in cm.drain() if e["proto"] == pia_connect.PROTO_NET]
    assert ack["dst"] == 0 and ack["pktid"] == 0


def _drops(minutes, ack_pktid):
    """Sim's own packet numbering over `minutes` of play; -> seconds at which the host dropped a
    data packet."""
    numbering = SimpleNamespace(_pktid_by_dst={})
    host = HostCounters()
    drops = []
    cm = _manager()
    for i in range(minutes * 60 * DATA_PER_SECOND):
        if i % ACK_EVERY == 0:
            cm.on_message(pia_connect.PROTO_NET, _property_update(i))
            for e in cm.drain():
                pktid = ack_pktid(e)
                host.accept(e["dst"], pktid if pktid is not None else Sim._next_pktid(numbering, e["dst"]))
        if not host.accept(HOST_VAR, Sim._next_pktid(numbering, HOST_VAR)):
            drops.append(i / DATA_PER_SECOND)
    return drops


def test_a_switch_led_session_outlives_eleven_minutes():
    assert _drops(30, lambda e: e["pktid"]) == []


def test_sequenced_acks_reproduce_the_eleven_minute_freeze():
    # The old ack numbering: past the first second nothing drops until ~683 s (32768 / (50 - 2)),
    # then every data packet does.
    drops = [t for t in _drops(13, lambda e: None) if t >= 1]
    assert drops and 660 <= drops[0] <= 700
    assert len(drops) > 0.9 * (13 * 60 - drops[0]) * DATA_PER_SECOND
