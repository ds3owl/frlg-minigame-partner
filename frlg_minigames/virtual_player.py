"""A player that exists only in the leader's command table.

The PC is the RFU parent; each virtual player is one row (multiplayer id 2..4) of the 70-byte
gRecvCmds table the parent broadcasts every VBlank. The real child (the Switch) cannot tell it from
a radio-linked child: it takes the player count from SEND_PLAYER_IDS [link_rfu_2.c:1138-1142],
copies all five rows into gRecvCmds [link_rfu_2.c:965-982] and runs each row's command through
RfuHandleReceiveCommand [link_rfu_2.c:1125-1215], which never checks a row against the radio slots.

A row behaves like a child's gSendCmd: one command per VBlank, idle when there is nothing to say.
Rows are written without the child's rolling tag; the parent strips it before publishing.
"""

from collections import deque

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames import packets
from frlg_minigames.net.gba import block, rfu

LINK_PLAYER_SEND_SIZE = 200   # BLOCK_REQ_SIZE_NONE [frlg/link/trade.py REQ_SIZE]


class VirtualPlayer:
    def __init__(self, multiplayer_id, link_player, *, trust_pia=True, block_repeat=2,
                 standby_echo_frames=4, log=lambda *a: None):
        if not 2 <= multiplayer_id <= 4:
            raise ValueError("virtual players take multiplayer ids 2..4")
        self.multiplayer_id = multiplayer_id
        self.link_player = link_player
        self.trust_pia = trust_pia
        self.block_repeat = block_repeat
        self.standby_echo_frames = standby_echo_frames
        self.log = log
        self._words = deque()
        self._blocks = deque()
        self._sender = None
        self._packet = None
        self.blocks_sent = 0
        self.standbys_answered = []

    @property
    def name(self):
        return self.link_player.name

    @property
    def busy(self):
        return bool(self._words or self._blocks or self._sender is not None)

    def link_player_block(self):
        """LocalLinkPlayerToBlock [link_rfu_2.c:1828; link.c:1600-1617] fills the front of the
        200-byte gBlockSendBuffer that SEND_BLOCK_REQ NONE sends whole [link.c:185-190]; both
        GameFreak magics are checked by LinkPlayerFromBlock [link.c:1619-1634]."""
        return linkplayer.build_block(self.link_player).ljust(LINK_PLAYER_SEND_SIZE, b"\x00")

    def queue_block(self, data, label=""):
        self._blocks.append((bytes(data), label))

    def queue_words(self, words, repeat=1):
        for _ in range(max(1, repeat)):
            self._words.append(list(words))

    def answer_standby(self, count):
        """READY_EXIT_STANDBY with the round the console broadcasts: the console marks this row
        ready only when word 1 equals its resendExitStandbyCount [link_rfu_2.c:1178-1180]."""
        self.standbys_answered.append(count)
        self.queue_words(rfu.exit_standby_words(count), self.standby_echo_frames)

    def answer_close(self, count):
        self.queue_words(rfu.close_link_words(count), self.standby_echo_frames)

    def set_packet(self, data):
        """Hold one SEND_PACKET on this row whenever nothing else is queued; None clears it.
        A member's game task sends its packet every frame [e.g. SendLinkData_Member,
        dodrio_berry_picking.c:1689-1705]."""
        self._packet = None if data is None else bytes(data)

    def next_words(self):
        if self._words:
            return self._words.popleft()
        if self._sender is None and self._blocks:
            data, _label = self._blocks.popleft()
            self._sender = block.BlockSender(data, owner=self.multiplayer_id,
                                             trust_pia=self.trust_pia,
                                             stream_repeat=self.block_repeat)
        if self._sender is not None:
            words = self._sender.tick(None)
            if self._sender.done:
                self._sender = None
                self.blocks_sent += 1
            if words[0] & 0xFFFF:
                return words
        if self._packet is not None:
            return packets.send_words(self._packet)
        return [0] * 7

    def next_row(self):
        """The 14-byte row for this VBlank."""
        return rfu.serialize(self.next_words())
