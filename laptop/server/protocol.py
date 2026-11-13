"""protocol.py - binary packets between the watches and the server (docs/interfaces.md, section 3.2).

All packets are little-endian and at most 20 bytes (BLE limit).
"""
import math
import struct
from dataclasses import dataclass

MAGIC_STATE = 0xC1   # watch -> laptop
MAGIC_WORLD = 0xC2   # laptop -> watch
MAGIC_EVENT = 0xC3   # laptop -> watch

STATE_FORMAT = "<BBBhhBBB"            # magic, playerId, seq, x, y, angle, flags, targetId = 10 bytes
STATE_SIZE = struct.calcsize(STATE_FORMAT)
NO_TARGET = 0xFF

FLAG_FIRED = 0x01
FLAG_READY = 0x02

GAME_LOBBY, GAME_PLAYING, GAME_ROUND_OVER, GAME_PAUSED = 0, 1, 2, 3
WORLD_HIT_ME, WORLD_I_HIT, WORLD_FLARE = 0x01, 0x02, 0x04
MAX_ENTITIES = 5

# EVENT packet (laptop -> watch): [0xC3, kind, a, b]
EVT_MATCH_START = 1   # a, b unused
EVT_MATCH_END = 2     # a = winner id, 0xFF = draw
EVT_RESPAWN = 3       # a, b = spawn x, y in 1/16 squares: the watch must move there
EVT_KILL = 4          # a = killer id, b = victim id


@dataclass
class State:
    player_id: int
    seq: int
    x: float          # map squares
    y: float
    angle: float      # radians
    fired: bool
    ready: bool
    target_id: int


def angle_to_u8(rad):
    return int(round((rad % (2 * math.pi)) / (2 * math.pi) * 256)) % 256


def u8_to_angle(v):
    return v * 2 * math.pi / 256


def parse_state(data: bytes):
    """Return a State, or None if the packet is not a valid STATE packet."""
    if len(data) != STATE_SIZE:
        return None
    magic, pid, seq, x, y, angle, flags, target = struct.unpack(STATE_FORMAT, data)
    if magic != MAGIC_STATE or pid > 3:
        return None
    return State(pid, seq, x / 256.0, y / 256.0, u8_to_angle(angle),
                 bool(flags & FLAG_FIRED), bool(flags & FLAG_READY), target)


def build_state(s: State) -> bytes:
    """Used by fake_watch / tests to make the packet a real watch would send."""
    flags = (FLAG_FIRED if s.fired else 0) | (FLAG_READY if s.ready else 0)
    return struct.pack(STATE_FORMAT, MAGIC_STATE, s.player_id, s.seq & 0xFF,
                       int(round(s.x * 256)), int(round(s.y * 256)),
                       angle_to_u8(s.angle), flags, s.target_id)


def build_world(game_state, my_health, flags, entities):
    """entities = [(id, x, y), ...] in map squares; at most 5 are sent (3 bytes each)."""
    entities = entities[:MAX_ENTITIES]
    out = struct.pack("<BBBBB", MAGIC_WORLD, game_state, my_health, flags, len(entities))
    for eid, x, y in entities:
        out += struct.pack("<BBB", eid, max(0, min(255, int(x * 16))), max(0, min(255, int(y * 16))))
    return out


def parse_world(data: bytes):
    magic, game_state, health, flags, count = struct.unpack("<BBBBB", data[:5])
    if magic != MAGIC_WORLD:
        return None
    ents = [struct.unpack("<BBB", data[5 + 3 * i: 8 + 3 * i]) for i in range(count)]
    return {"game_state": game_state, "health": health, "flags": flags,
            "entities": [(e, x / 16.0, y / 16.0) for e, x, y in ents]}


def pos_to_byte(v):
    """Map position (squares) -> 1/16-square byte, clamped to 0..255."""
    return max(0, min(255, int(v * 16)))


def build_event(kind, a=0, b=0):
    return struct.pack("<BBBB", MAGIC_EVENT, kind, a & 0xFF, b & 0xFF)


def parse_event(data: bytes):
    """Return (kind, a, b), or None if this is not an EVENT packet."""
    if len(data) != 4 or data[0] != MAGIC_EVENT:
        return None
    return data[1], data[2], data[3]
