"""watch_link.py - the laptop plays Player B in a duel against one TinyBoy watch (Player A) over Bluetooth.

The watch runs the normal firmware (firmware/tinyboy): MULTIPLAYER -> HOST (A). It waits for a Player B;
this program connects to it and behaves exactly like a second watch would:

  * packet:        the 12-byte duel packet, docs/interfaces.md section 3.1 (magic 0xB0)
  * turn-taking:   B speaks first; B sends again when the watch has answered and 100 ms have passed,
                   or after 300 ms without an answer (the answer got lost)
  * shooter decides hits:  B raises its own hitsLanded; B's damage = the watch's hitsLanded,
                   but only when the watch's packet is for the SAME round number
  * rounds:        2 hits ends a round. Like the firmware, B keeps sending the final counts during the
                   2.5 s "round over" screen, then starts the next round (and catches up if the watch is
                   already on a later round). Match length = the watch's "rounds" setting.
  * angle:         same formula as the firmware: (atan2(dirY, dirX) + pi) / (2 pi) * 255  (west = 255)

Player B is the server bot (duel_bot.py): BFS walking, turns to face the watch player, shoots when it
has line of sight; this program decides whether each of its shots lands. --still = step 1 (stands still).

Match events: the same EVT,... lines the watch prints (docs/interfaces.md section 3.1b), as Player B,
go to the screen; every other line printed here starts with something else, so bridge.py ignores it.

    cd laptop
    python -m server.watch_link --scan                     # list nearby Bluetooth devices
    python -m server.watch_link                            # play against the server bot (normal)
    python -m server.watch_link --difficulty hard          # easy / normal / hard
    python -m server.watch_link --still                    # step 1: Player B stands still
    python -m server.watch_link --events match.log         # also save the EVT lines for bridge.py --replay
    python -m server.watch_link --verbose                  # also print every packet

BLE code based on laptop/ble_bridge_test.py (bleak + the TinyCircuits Nordic UART example).
"""
import argparse
import asyncio
import math
import struct
import time
from collections import deque
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants (docs/interfaces.md sections 2 and 3.1, firmware/tinyboy/tinyboy.ino)
# ---------------------------------------------------------------------------
WATCH_A_ADDRESS = "0A:80:E1:00:34:12"   # Player A's fixed address, as Windows shows it
WATCH_A_NAME = "TinyboyA"               # what Player A advertises
UART_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"    # laptop -> watch
UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # watch -> laptop

PACKET_MAGIC = 0xB0
PACKET_FORMAT = "<BBBhhBBBBB"   # magic, seq, round, x, y, angle, hitsLanded, flags, shots, rounds
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)   # 12
FLAG_FIRED = 0x01
FLAG_PAUSED = 0x02

SEND_INTERVAL_S = 0.100    # firmware SEND_INTERVAL_MS
REPLY_TIMEOUT_S = 0.300    # firmware REPLY_TIMEOUT_MS
ROUND_OVER_S = 2.5         # firmware ROUND_OVER_MS
FIRED_FLAG_S = 0.100       # the fired flag is on for 100 ms after a shot
MAX_HITS = 2               # hits that lose a round
VALID_ROUNDS = (1, 3, 5)   # the host's "rounds to win" setting

B_SPAWN = (13.5, 13.5)     # bottom-right corner ...
B_FACING = math.pi         # ... facing west (same as the firmware's Player B)


# ---------------------------------------------------------------------------
# The duel packet
# ---------------------------------------------------------------------------
@dataclass
class DuelPacket:
    seq: int
    round: int
    x: float            # map squares
    y: float
    angle: float        # radians, 0 = east, pi = west (same as atan2 on the watch)
    hits_landed: int
    flags: int
    shots: int
    rounds: int


def angle_to_byte(rad):
    """The firmware formula: (uint8_t)((atan2f(dirY, dirX) + PI) / (2 * PI) * 255)."""
    a = math.atan2(math.sin(rad), math.cos(rad))      # same range as atan2 on the watch: -pi..pi
    return max(0, min(255, int((a + math.pi) / (2 * math.pi) * 255)))


def byte_to_angle(b):
    return b / 255 * 2 * math.pi - math.pi


def pack(p: DuelPacket) -> bytes:
    return struct.pack(PACKET_FORMAT, PACKET_MAGIC, p.seq & 0xFF, p.round & 0xFF,
                       int(p.x * 256), int(p.y * 256), angle_to_byte(p.angle),
                       p.hits_landed & 0xFF, p.flags & 0xFF, p.shots & 0xFF, p.rounds & 0xFF)


def unpack(data: bytes):
    """Return a DuelPacket, or None if this is not a duel packet (wrong size or magic)."""
    if len(data) != PACKET_SIZE or data[0] != PACKET_MAGIC:
        return None
    _, seq, rnd, x, y, angle, hits, flags, shots, rounds = struct.unpack(PACKET_FORMAT, data)
    return DuelPacket(seq, rnd, x / 256.0, y / 256.0, byte_to_angle(angle), hits, flags, shots, rounds)


# ---------------------------------------------------------------------------
# Turn-taking: Player B's send rule from the firmware
# ---------------------------------------------------------------------------
class TurnTaker:
    def __init__(self):
        self.last_send = None
        self.got_reply = True     # B speaks first
        self.sent = self.replies = self.timeouts = 0

    def should_send(self, now):
        if self.last_send is None:
            return True
        waited = now - self.last_send
        return (self.got_reply and waited >= SEND_INTERVAL_S) or waited >= REPLY_TIMEOUT_S

    def on_send(self, now):
        if not self.got_reply and self.last_send is not None:
            self.timeouts += 1    # sending without an answer to the previous packet
        self.last_send, self.got_reply = now, False
        self.sent += 1

    def on_reply(self):
        self.got_reply = True
        self.replies += 1

    def wait_time(self, now):
        """How long until should_send() can become true without a new reply."""
        if self.last_send is None:
            return 0.0
        limit = SEND_INTERVAL_S if self.got_reply else REPLY_TIMEOUT_S
        return max(0.0, self.last_send + limit - now)


# ---------------------------------------------------------------------------
# Player B's side of the duel rules (mirrors startRound / endRound / catch-up in the firmware)
# ---------------------------------------------------------------------------
class StandStill:
    """Step 1 brain: Player B does nothing - it just stands at its spawn and never fires."""

    def reset_round(self, duel, now):
        pass

    def update(self, duel, dt, now):
        pass


class MatchEvents:
    """The EVT lines Player B's firmware prints (docs/interfaces.md section 3.1b), as Player B.
    `emit` gets one line at a time, without the newline."""

    def __init__(self, emit=None):
        self.emit = emit or (lambda line: None)

    def _out(self, *fields):
        self.emit("EVT," + ",".join(str(f) for f in fields))

    def hello(self):
        self._out("HELLO", "B")

    def match_start(self):
        self._out("MATCH_START", "B")

    def round_start(self, rnd):
        self._out("ROUND_START", rnd)

    def shot(self, hit):
        self._out("SHOT", "B", 1 if hit else 0)

    def hit(self, shooter, target, hp_left):
        self._out("HIT", shooter, target, hp_left)

    def pos(self, a_xy, b_xy):
        """A first (left out until the watch's first packet), then B. Hundredths of a square."""
        fields = ["POS"]
        if a_xy is not None:
            fields += ["A", int(a_xy[0] * 100), int(a_xy[1] * 100)]
        fields += ["B", int(b_xy[0] * 100), int(b_xy[1] * 100)]
        self._out(*fields)

    def round_end(self, rnd, winner, hits_a, hits_b, shots_a, shots_b, ms):
        self._out("ROUND_END", rnd, winner, hits_a, hits_b, shots_a, shots_b, ms)

    def match_end(self, winner, rounds_a, rounds_b):
        self._out("MATCH_END", winner, rounds_a, rounds_b)

    def match_abort(self):
        self._out("MATCH_ABORT")


class DuelB:
    PLAYING, ROUND_OVER, MATCH_OVER = "playing", "round_over", "match_over"
    POS_EVERY_S = 0.2                   # firmware POS_LOG_INTERVAL_MS: 5 positions per second

    def __init__(self, brain=None, events=None):
        self.brain = brain or StandStill()
        self.events = events or MatchEvents()
        self.match_active = False       # a match has started and not ended (MATCH_ABORT if the link drops)
        self.round_started_at = 0.0
        self.a_shots = 0                # the watch's shots this round (from its packets)
        self.last_hits_taken = 0        # for HIT lines when the watch's count goes up
        self.last_pos_log = -99.0
        self.state = None
        self.round = 0
        self.rounds_to_win = 3          # replaced by the watch's setting from its first packet
        self.rounds_b = self.rounds_a = 0
        self.hits_landed = 0            # my hits on the watch player this round
        self.hits_taken = 0             # the watch's hitsLanded for THIS round
        self.shots = 0
        self.last_fire = -99.0
        self.x, self.y = B_SPAWN
        self.angle = B_FACING
        self.seq = 0
        self.a = None                   # latest packet from the watch
        self.a_paused = False
        self.state_since = 0.0
        self.log = []                   # human-readable round results

    # ---- lifecycle ----
    def start(self, now):
        """Called when the Bluetooth link comes up (the watch starts round 1 at that moment too)."""
        self.round = 0
        self.rounds_b = self.rounds_a = 0
        self.match_active = False
        self.events.hello()
        self.start_round(now)

    def start_round(self, now):
        self.round += 1
        if not self.match_active:
            self.match_active = True
            self.events.match_start()
        self.events.round_start(self.round)
        self.hits_landed = self.hits_taken = self.shots = self.a_shots = 0
        self.last_hits_taken = 0
        self.round_started_at = now
        self.x, self.y = B_SPAWN
        self.angle = B_FACING
        self.last_fire = -99.0
        self.state, self.state_since = self.PLAYING, now
        self.brain.reset_round(self, now)

    def end_round(self, result, now):           # 1 = B won, -1 = B lost, 0 = draw
        if result > 0:
            self.rounds_b += 1
        elif result < 0:
            self.rounds_a += 1
        word = {1: "B wins", -1: "A wins", 0: "draw"}[result]
        self.log.append(f"round {self.round}: {word} (A {self.rounds_a} - {self.rounds_b} B)")
        over = self.rounds_a >= self.rounds_to_win or self.rounds_b >= self.rounds_to_win
        self.events.round_end(self.round, {1: "B", -1: "A", 0: "D"}[result],
                              self.hits_taken, self.hits_landed, self.a_shots, self.shots,
                              int((now - self.round_started_at) * 1000))
        self.state = self.MATCH_OVER if over else self.ROUND_OVER
        self.state_since = now
        if over:
            winner = "B" if self.rounds_b >= self.rounds_to_win else "A"
            self.events.match_end(winner, self.rounds_a, self.rounds_b)
            self.match_active = False
            self.log.append(f"match over: {winner} wins")

    def abort(self):
        """The link dropped (or we were stopped) in the middle of a match."""
        if self.match_active:
            self.events.match_abort()
            self.match_active = False

    # ---- input from the watch ----
    def on_packet(self, pkt: DuelPacket, now):
        self.a = pkt
        if pkt.rounds in VALID_ROUNDS:
            self.rounds_to_win = pkt.rounds                 # the host decides
        self.a_paused = bool(pkt.flags & FLAG_PAUSED)
        if pkt.round == self.round:
            self.hits_taken = pkt.hits_landed               # only this round's count is damage
            self.a_shots = pkt.shots

    # ---- actions a brain can take ----
    def fire(self, now, hit):
        """The brain fired. `hit` = it decided the shot hit the watch player (shooter decides)."""
        self.last_fire = now
        self.shots += 1
        landed = hit and self.hits_landed < MAX_HITS
        self.events.shot(landed)
        if landed:
            self.hits_landed += 1
            self.events.hit("B", "A", MAX_HITS - self.hits_landed)

    # ---- every tick ----
    def update(self, now, dt):
        if self.state == self.MATCH_OVER:
            return
        if self.state == self.ROUND_OVER:
            if now - self.state_since >= ROUND_OVER_S:
                self.start_round(now)
            return
        if self.a is not None and self.a.round > self.round:   # the watch already moved on: catch up
            self.round = self.a.round - 1
            self.start_round(now)
        if self.a_paused:                                      # the watch player paused: freeze
            return
        self.brain.update(self, dt, now)
        if self.hits_taken > self.last_hits_taken:             # the watch player landed hits on B
            for h in range(self.last_hits_taken + 1, self.hits_taken + 1):
                self.events.hit("A", "B", MAX_HITS - h)
            self.last_hits_taken = self.hits_taken
        won, lost = self.hits_landed >= MAX_HITS, self.hits_taken >= MAX_HITS
        if won or lost:
            self.end_round(0 if (won and lost) else (1 if won else -1), now)
            return
        if now - self.last_pos_log >= self.POS_EVERY_S:
            self.last_pos_log = now
            a_xy = (self.a.x, self.a.y) if self.a is not None else None
            self.events.pos(a_xy, (self.x, self.y))

    # ---- output to the watch ----
    def packet(self, now) -> DuelPacket:
        flags = FLAG_FIRED if now - self.last_fire < FIRED_FLAG_S else 0
        p = DuelPacket(self.seq, self.round, self.x, self.y, self.angle,
                       self.hits_landed, flags, self.shots, self.rounds_to_win)
        self.seq = (self.seq + 1) & 0xFF
        return p

    def status(self):
        a = self.a
        where = f"A at ({a.x:.1f},{a.y:.1f}) round {a.round}" if a else "nothing from A yet"
        return (f"{self.state:10} round {self.round}  hits B->A {self.hits_landed}  A->B {self.hits_taken}  "
                f"rounds A {self.rounds_a} - {self.rounds_b} B (to {self.rounds_to_win})  | {where}")


# ---------------------------------------------------------------------------
# The link loop - works with any connected client that has bleak's write/notify methods
# ---------------------------------------------------------------------------
async def run_session(client, duel, turns=None, clock=time.monotonic, verbose=False,
                      out=print, stop=None, report_every=1.0):
    """Play until the watch disconnects (or `stop` is set). Returns a stats dict."""
    turns = turns or TurnTaker()
    loop = asyncio.get_running_loop()
    inbox = deque()
    wake = asyncio.Event()
    stats = {"bad": 0}

    def on_notify(_char, data):          # may be called from bleak's thread: hand over safely
        inbox.append((clock(), bytes(data)))
        loop.call_soon_threadsafe(wake.set)

    await client.start_notify(UART_NOTIFY, on_notify)
    duel.start(clock())
    try:
        await _session_loop(client, duel, turns, clock, verbose, out, stop, report_every, inbox, wake, stats)
    finally:
        duel.abort()                     # link dropped / Ctrl+C mid-match -> EVT,MATCH_ABORT
    stats.update(sent=turns.sent, replies=turns.replies, timeouts=turns.timeouts)
    return stats


async def _session_loop(client, duel, turns, clock, verbose, out, stop, report_every, inbox, wake, stats):
    last = last_report = clock()
    while client.is_connected and not (stop is not None and stop.is_set()):
        while inbox:
            t, data = inbox.popleft()
            pkt = unpack(data)
            if pkt is None:
                stats["bad"] += 1
                continue
            turns.on_reply()
            duel.on_packet(pkt, t)
            if verbose:
                out(f"<- A {data.hex(' ')}  round {pkt.round} hits {pkt.hits_landed} pos ({pkt.x:.2f},{pkt.y:.2f})")
        now = clock()
        duel.update(now, min(now - last, 0.25))
        last = now
        if turns.should_send(now):
            data = pack(duel.packet(now))
            turns.on_send(now)
            try:
                await client.write_gatt_char(UART_WRITE, data, response=False)
            except Exception as e:                   # link dropped mid-write
                out(f"[write failed: {e}]")
                break
            if verbose:
                out(f"-> B {data.hex(' ')}")
        if now - last_report >= report_every:
            last_report = now
            out(f"[link] sent {turns.sent} replies {turns.replies} no-reply {turns.timeouts} bad {stats['bad']} | {duel.status()}")
        wake.clear()
        try:                                         # sleep until a reply arrives or it is time to send
            await asyncio.wait_for(wake.wait(), timeout=max(0.005, min(0.02, turns.wait_time(clock()))))
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Bluetooth (bleak)
# ---------------------------------------------------------------------------
async def scan(timeout=8.0, out=print):
    from bleak import BleakScanner
    out(f"Scanning for {timeout:.0f} s ...")
    devices = await BleakScanner.discover(timeout=timeout)
    for d in sorted(devices, key=lambda d: d.address):
        mark = "  <- Player A" if d.address.upper() == WATCH_A_ADDRESS or d.name == WATCH_A_NAME else ""
        out(f"  {d.address}  {d.name or '(no name)'}{mark}")
    return devices


async def find_watch(address, timeout=10.0, out=print):
    devices = await scan(timeout, out=lambda s: None)
    for d in devices:
        if d.address.upper() == address.upper():
            return d
    for d in devices:                                # fall back to the advertised name
        if d.name == WATCH_A_NAME:
            out(f"Found {WATCH_A_NAME} at {d.address} (not the expected {address})")
            return d
    return None


async def main_async(args, out=print):
    if args.scan:
        await scan(out=out)
        return 0
    from bleak import BleakClient
    out(f"Looking for Player A ({args.address}) ... make sure the watch shows 'Waiting for B'.")
    device = await find_watch(args.address, out=out)
    if device is None:
        out("Not found. Check: Bluetooth is on, the watch is on MULTIPLAYER -> HOST (A), "
            "and nothing else (nRF Connect, another watch) is connected to it. Try --scan.")
        return 1
    out(f"Found {device.name} ({device.address}), connecting ...")
    brain = StandStill() if args.still else make_bot(args.difficulty, args.seed)
    emit, close_events = make_emitter(args.events)
    try:
        stop = asyncio.Event()
        async with BleakClient(device, disconnected_callback=lambda _c: stop.set()) as client:
            who = "standing still" if args.still else f"the server bot ({args.difficulty})"
            out(f"Connected - the watch should now leave 'Waiting for B'. Player B is {who}. Ctrl+C to stop.")
            duel = DuelB(brain, MatchEvents(emit))
            stats = await run_session(client, duel, verbose=args.verbose, out=out, stop=stop)
    finally:
        close_events()
    out("Disconnected.")
    out(f"Summary: sent {stats['sent']}, replies {stats['replies']}, sent without reply {stats['timeouts']}, "
        f"bad packets {stats['bad']}")
    for line in duel.log:
        out("  " + line)
    return 0


def make_emitter(events_path=None, echo=print):
    """EVT lines go to the screen straight away (for bridge.py) and, with --events, to a file too.
    Returns (emit, close)."""
    f = open(events_path, "a", encoding="utf-8") if events_path else None

    def emit(line):
        echo(line, flush=True)
        if f:
            f.write(line + "\n")
            f.flush()

    def close():
        if f:
            f.close()
    return emit, close


def make_bot(difficulty, seed=None):
    from .duel_bot import DuelBot
    import random
    return DuelBot(difficulty, random.Random(seed))


def main():
    ap = argparse.ArgumentParser(description="Laptop = Player B in a duel against one TinyBoy watch")
    ap.add_argument("--address", default=WATCH_A_ADDRESS, help="Player A's Bluetooth address")
    ap.add_argument("--scan", action="store_true", help="only list nearby Bluetooth devices")
    ap.add_argument("--verbose", action="store_true", help="print every packet")
    ap.add_argument("--still", action="store_true", help="step 1: Player B stands still and never shoots")
    ap.add_argument("--difficulty", choices=("easy", "normal", "hard"), default="normal",
                    help="how well the server bot aims and reacts (default normal)")
    ap.add_argument("--seed", type=int, default=None, help="random seed for the bot (repeatable matches)")
    ap.add_argument("--events", metavar="FILE",
                    help="also append the EVT lines to FILE (works with: bridge.py --replay FILE)")
    args = ap.parse_args()
    try:
        from bleak.exc import BleakError
    except ImportError:
        print("bleak is not installed. Run:  pip install bleak")
        return 1
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except (BleakError, asyncio.TimeoutError, OSError) as e:     # Bluetooth off, connect failed, ...
        print(f"Bluetooth problem: {e}")
        print("Check: Bluetooth is switched on in Windows (Settings > Bluetooth & devices), and the watch "
              "is on MULTIPLAYER -> HOST (A) with nothing else connected to it.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
