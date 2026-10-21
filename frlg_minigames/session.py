"""The FRLG leader session (net/frlg/link/host_session.py) with the two hooks the minigames
need, added by subclassing.

1. Virtual players: in UNI the leader's command table carries the extra rows the activity returns
   from extra_rows() (multiplayer ids 2..4, players that exist only in this table). The child
   copies all five rows [link_rfu_2.c:965-982] and takes the player count from SEND_PLAYER_IDS
   alone [link_rfu_2.c:1138-1142].
2. Game clock: a VBlank whose row is held back (send window full, console ack behind) still
   advances an activity that has idle_frame().

The link layer itself stays as it is."""

from frlg_minigames.net.frlg.link.host_session import HostSession
from frlg_minigames.net.gba import rfu
from frlg_minigames.net.gba.rfu_leader import KEEPALIVE, UNI, RFULeader


class MinigameRFULeader(RFULeader):
    """RFULeader whose UNI table can carry up to three more rows."""

    extra_rows = None  # callable returning 14-byte rows, set by MinigameHostSession

    def tick(self, parent_words=None):
        rows = self.extra_rows() if (self.extra_rows and parent_words is not None) else None
        if not rows or self._pending:
            return super().tick(parent_words)
        if self.state == KEEPALIVE:
            if self._keepalive_left > 0:
                return super().tick(parent_words)
            self.state = UNI
        if self.state != UNI:
            return super().tick(parent_words)
        if isinstance(parent_words, (bytes, bytearray)):
            parent_cmd = bytes(parent_words)[:rfu.COMM_SLOT_LENGTH].ljust(rfu.COMM_SLOT_LENGTH, b"\x00")
        else:
            parent_cmd = rfu.serialize(parent_words)
        table_rows = [parent_cmd, self._echo.next_row()]
        table_rows.extend(bytes(r)[:rfu.COMM_SLOT_LENGTH].ljust(rfu.COMM_SLOT_LENGTH, b"\x00")
                          for r in list(rows)[:3])
        table = rfu.pack_recv_cmds(table_rows)
        self.uni_out += 1
        return self._wrap_parent_t(rfu.parent_uni_slot(table, self.bm_slot))


class MinigameHostSession(HostSession):
    """HostSession for an activity with extra_rows() and/or idle_frame()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rfu.__class__ = MinigameRFULeader
        self.rfu.extra_rows = getattr(self.activity, "extra_rows", None)

    def tick(self, now_ms):
        window, backlog = self.window_full_ticks, self.backlog_ticks
        out = super().tick(now_ms)
        # the counters only grow on a held VBlank (a drain resets them to 0)
        if self.window_full_ticks > window or self.backlog_ticks > backlog:
            idle = getattr(self.activity, "idle_frame", None)
            if idle is not None and self.rfu.state == UNI:
                idle()
        return out
