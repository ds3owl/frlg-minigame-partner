"""Minigame packets carried in RFUCMD_SEND_PACKET rows.

Rfu_SendPacket copies sizeof(gRfu.packet) = 12 bytes from the packet struct into gSendCmd[1..6]
[link_rfu_2.c:1311-1313, 1324-1330; include/link_rfu.h:33]; the receiver memcpy()s or casts
gRecvCmds[id][1] back to the same struct. Offsets below are the agbcc (GCC, ARM, little-endian)
layout of each struct: natural alignment, bitfields allocated from bit 0 and shared with the
previous unit when they fit. A struct shorter than 12 bytes sends whatever follows it on the
stack; those bytes are zero here and no reader looks at them.
"""

from frlg_minigames.net.gba import rfu

PACKET_BYTES = 12

# --- Pokemon Jump [src/pokemon_jump.c] -----------------------------------------------------------

# enum at pokemon_jump.c:45-48
JUMP_PACKET_MON_INFO = 1
JUMP_PACKET_UNUSED = 2
JUMP_PACKET_LEADER_STATE = 3
JUMP_PACKET_MEMBER_STATE = 4

# monState / jumpState [pokemon_jump.c:104-114]
MONSTATE_NORMAL, MONSTATE_JUMP, MONSTATE_HIT = 0, 1, 2
JUMPSTATE_NONE, JUMPSTATE_SUCCESS, JUMPSTATE_FAILURE = 0, 1, 2


def words_from_bytes(data):
    """12 bytes -> the six u16 words of gRfu.packet."""
    data = bytes(data)[:PACKET_BYTES].ljust(PACKET_BYTES, b"\x00")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, PACKET_BYTES, 2)]


def bytes_from_words(words):
    """gRecvCmds[id][1..6] -> 12 bytes."""
    words = list(words)[:6] + [0] * (6 - len(list(words)[:6]))
    return b"".join((int(w) & 0xFFFF).to_bytes(2, "little") for w in words)


def send_words(data):
    """The seven-word SEND_PACKET row for one 12-byte packet."""
    return rfu.send_packet_words(words_from_bytes(data))


def packet_bytes_of_slot(slot):
    """A 14-byte command row -> its 12 packet bytes, or None if the row is not SEND_PACKET."""
    rec = rfu.parse_slot(slot)
    if rec is None or rec["op"] != rfu.SEND_PACKET:
        return None
    return bytes_from_words(rec["packet"])


def encode_jump_mon_info(species, personality, ot_id):
    """struct MonInfoPacket {u8 id; u16 species; u32 personality; u32 otId;}
    [pokemon_jump.c:2707-2722]: id@0, species@2, personality@4, otId@8."""
    out = bytearray(PACKET_BYTES)
    out[0] = JUMP_PACKET_MON_INFO
    out[2:4] = (species & 0xFFFF).to_bytes(2, "little")
    out[4:8] = (personality & 0xFFFFFFFF).to_bytes(4, "little")
    out[8:12] = (ot_id & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


def decode_jump_mon_info(data):
    """None unless id == PACKET_MON_INFO [RecvPacket_MonInfo, pokemon_jump.c:2724-2741]."""
    if data is None or data[0] != JUMP_PACKET_MON_INFO:
        return None
    return {"species": int.from_bytes(data[2:4], "little"),
            "personality": int.from_bytes(data[4:8], "little"),
            "ot_id": int.from_bytes(data[8:12], "little")}


def encode_jump_leader_state(*, func_id, mon_state, received_bonus_flags, jump_state,
                             jump_time_start, data, jumps_in_row, jump_score):
    """struct LeaderStatePacket [pokemon_jump.c:2761-2772]: id@0 funcId@1 monState@2,
    byte 3 = receivedBonusFlags:5 | jumpState:3 << 5, jumpTimeStart@4, vineTimer(comm.data)@6,
    u32@8 = jumpsInRow:15 | jumpScore:17 << 15. `data` is comm.data: the RNG seed during the intro,
    the round timer while waiting [pokemon_jump.c:1302-1352]."""
    out = bytearray(PACKET_BYTES)
    out[0] = JUMP_PACKET_LEADER_STATE
    out[1] = func_id & 0xFF
    out[2] = mon_state & 0xFF
    out[3] = (received_bonus_flags & 0x1F) | ((jump_state & 0x7) << 5)
    out[4:6] = (jump_time_start & 0xFFFF).to_bytes(2, "little")
    out[6:8] = (data & 0xFFFF).to_bytes(2, "little")
    packed = (jumps_in_row & 0x7FFF) | ((jump_score & 0x1FFFF) << 15)
    out[8:12] = packed.to_bytes(4, "little")
    return bytes(out)


def decode_jump_leader_state(data):
    """None unless id == PACKET_LEADER_STATE [RecvPacket_LeaderState, pokemon_jump.c:2789-2809]."""
    if data is None or data[0] != JUMP_PACKET_LEADER_STATE:
        return None
    packed = int.from_bytes(data[8:12], "little")
    return {"func_id": data[1], "mon_state": data[2],
            "received_bonus_flags": data[3] & 0x1F, "jump_state": (data[3] >> 5) & 0x7,
            "jump_time_start": int.from_bytes(data[4:6], "little"),
            "data": int.from_bytes(data[6:8], "little"),
            "jumps_in_row": packed & 0x7FFF, "jump_score": (packed >> 15) & 0x1FFFF}


def encode_jump_member_state(*, mon_state, jump_state, func_finished, jump_time_start, func_id,
                             play_again_state):
    """struct MemberStatePacket [pokemon_jump.c:2812-2821]: id@0 monState@1 jumpState@2
    funcFinished@3 jumpTimeStart@4 funcId@6 (pad@7) playAgainState@8; sizeof 10."""
    out = bytearray(PACKET_BYTES)
    out[0] = JUMP_PACKET_MEMBER_STATE
    out[1] = mon_state & 0xFF
    out[2] = jump_state & 0xFF
    out[3] = 1 if func_finished else 0
    out[4:6] = (jump_time_start & 0xFFFF).to_bytes(2, "little")
    out[6] = func_id & 0xFF
    out[8:10] = (play_again_state & 0xFFFF).to_bytes(2, "little")
    return bytes(out)


def decode_jump_member_state(data):
    """None unless id == PACKET_MEMBER_STATE [pokemon_jump.c:2836-2879]."""
    if data is None or data[0] != JUMP_PACKET_MEMBER_STATE:
        return None
    return {"mon_state": data[1], "jump_state": data[2], "func_finished": bool(data[3]),
            "jump_time_start": int.from_bytes(data[4:6], "little"), "func_id": data[6],
            "play_again_state": int.from_bytes(data[8:10], "little")}


# --- Dodrio Berry Picking [src/dodrio_berry_picking_comm.c] -------------------------------------

# enum at dodrio_berry_picking_comm.c:5-11
BERRY_PACKET_READY_START = 1
BERRY_PACKET_GAME_STATE = 2
BERRY_PACKET_PICK_STATE = 3
BERRY_PACKET_READY_END = 4

NUM_BERRY_COLUMNS_SENT = 10
MAX_RFU_PLAYERS = 5


def encode_berry_ready_start(ready=True):
    """struct ReadyToStartPacket {u8 id; bool8 ALIGNED(4) ready;}: ready@4
    [dodrio_berry_picking_comm.c:13-17, 74-80]."""
    out = bytearray(PACKET_BYTES)
    out[0] = BERRY_PACKET_READY_START
    out[4] = 1 if ready else 0
    return bytes(out)


def decode_berry_ready_start(data):
    """-> the ready flag, or None if not a READY_START packet [comm.c:82-94]."""
    if data is None or data[0] != BERRY_PACKET_READY_START:
        return None
    return bool(data[4])


def encode_berry_pick_state(pick_state):
    """struct PickStatePacket {u8 id; u8 ALIGNED(4) pickState;}: pickState@4 [comm.c:62-66,
    229-235]."""
    out = bytearray(PACKET_BYTES)
    out[0] = BERRY_PACKET_PICK_STATE
    out[4] = pick_state & 0xFF
    return bytes(out)


def decode_berry_pick_state(data):
    if data is None or data[0] != BERRY_PACKET_PICK_STATE:
        return None
    return data[4]


def encode_berry_ready_end(ready=True):
    """struct ReadyToEndPacket {u8 id; bool32 ready;}: ready@4 as u32 [comm.c:68-72, 254-260]."""
    out = bytearray(PACKET_BYTES)
    out[0] = BERRY_PACKET_READY_END
    out[4:8] = (1 if ready else 0).to_bytes(4, "little")
    return bytes(out)


def decode_berry_ready_end(data):
    if data is None or data[0] != BERRY_PACKET_READY_END:
        return None
    return bool(int.from_bytes(data[4:8], "little"))


def encode_berry_game_state(*, fall_dist, berry_ids, pick_states, ate_berry, missed_berry,
                            num_gray_squares, berries_falling, all_ready_to_end):
    """struct GameStatePacket [comm.c:19-60]:
        byte 1      fallDist col0 | col1 << 4                          (u8 unit)
        bytes 2-3   fallDist col2..col5, 4 bits each                   (u16 unit)
        bytes 4-5   fallDist col6..col9                                (u16 unit)
        bytes 6-7   berryId col0..col7, 2 bits each                    (u16 unit)
        byte 8      berryId col8, col9, pickState p1, p2 (2 bits each) (u8 unit)
        byte 9      pickState p3, p4, p5, ateBerry p1, p2
        byte 10     ateBerry p3, p4, p5, numGraySquares:5
        byte 11     allReadyToEnd, berriesFalling, missedBerry p1..p5
    Player n is multiplayer id n-1. UNCONFIRMED against a live capture; see docs/design.md.
    """
    fall = [int(x) & 0xF for x in list(fall_dist)[:10]] + [0] * (10 - len(list(fall_dist)[:10]))
    ids = [int(x) & 0x3 for x in list(berry_ids)[:10]] + [0] * (10 - len(list(berry_ids)[:10]))
    picks = [int(x) & 0x3 for x in list(pick_states)[:5]] + [0] * (5 - len(list(pick_states)[:5]))
    ate = [1 if x else 0 for x in list(ate_berry)[:5]] + [0] * (5 - len(list(ate_berry)[:5]))
    missed = ([1 if x else 0 for x in list(missed_berry)[:5]]
              + [0] * (5 - len(list(missed_berry)[:5])))
    out = bytearray(PACKET_BYTES)
    out[0] = BERRY_PACKET_GAME_STATE
    out[1] = fall[0] | (fall[1] << 4)
    w = fall[2] | (fall[3] << 4) | (fall[4] << 8) | (fall[5] << 12)
    out[2:4] = w.to_bytes(2, "little")
    w = fall[6] | (fall[7] << 4) | (fall[8] << 8) | (fall[9] << 12)
    out[4:6] = w.to_bytes(2, "little")
    w = 0
    for i in range(8):
        w |= ids[i] << (2 * i)
    out[6:8] = w.to_bytes(2, "little")
    out[8] = ids[8] | (ids[9] << 2) | (picks[0] << 4) | (picks[1] << 6)
    out[9] = picks[2] | (picks[3] << 2) | (picks[4] << 4) | (ate[0] << 6) | (ate[1] << 7)
    out[10] = ate[2] | (ate[3] << 1) | (ate[4] << 2) | ((num_gray_squares & 0x1F) << 3)
    out[11] = ((1 if all_ready_to_end else 0) | ((1 if berries_falling else 0) << 1)
               | (missed[0] << 2) | (missed[1] << 3) | (missed[2] << 4) | (missed[3] << 5)
               | (missed[4] << 6))
    return bytes(out)


def decode_berry_game_state(data):
    """Inverse of encode_berry_game_state; None unless id == PACKET_GAME_STATE."""
    if data is None or data[0] != BERRY_PACKET_GAME_STATE:
        return None
    w1 = int.from_bytes(data[2:4], "little")
    w2 = int.from_bytes(data[4:6], "little")
    w3 = int.from_bytes(data[6:8], "little")
    fall = [data[1] & 0xF, data[1] >> 4] + [(w1 >> (4 * i)) & 0xF for i in range(4)] \
        + [(w2 >> (4 * i)) & 0xF for i in range(4)]
    ids = [(w3 >> (2 * i)) & 0x3 for i in range(8)] + [data[8] & 0x3, (data[8] >> 2) & 0x3]
    picks = [(data[8] >> 4) & 0x3, (data[8] >> 6) & 0x3, data[9] & 0x3, (data[9] >> 2) & 0x3,
             (data[9] >> 4) & 0x3]
    ate = [bool((data[9] >> 6) & 1), bool((data[9] >> 7) & 1), bool(data[10] & 1),
           bool((data[10] >> 1) & 1), bool((data[10] >> 2) & 1)]
    missed = [bool((data[11] >> (2 + i)) & 1) for i in range(5)]
    return {"fall_dist": fall, "berry_ids": ids, "pick_states": picks, "ate_berry": ate,
            "missed_berry": missed, "num_gray_squares": (data[10] >> 3) & 0x1F,
            "all_ready_to_end": bool(data[11] & 1), "berries_falling": bool((data[11] >> 1) & 1)}
