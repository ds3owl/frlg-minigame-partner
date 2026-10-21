"""The app's steps from a terminal (for scripts and AI tools); the desktop app does the same.

    python -m frlg_minigames.cli ports
    python -m frlg_minigames.cli prepare --port COM7
    python -m frlg_minigames.cli jump  --port COM7 --keys C:\\path\\prod.keys
    python -m frlg_minigames.cli berry --port COM7 --keys C:\\path\\prod.keys

`jump`: on the Switch, Two Island -> Joyful Game Corner -> Pokemon Jump attendant -> pick a
Pokemon -> BECOME LEADER; the PC (Ash) joins. `berry`: the PC opens a group; on the Switch,
Dodrio Berry Picking attendant -> pick your Dodrio -> JOIN GROUP -> choose Ash. Ctrl+C stops.
Status lines go to stdout; --log FILE keeps the detailed log, --capture FILE the packet capture."""

import argparse
import os
import sys
import threading
import time

from frlg_minigames import board
from frlg_minigames.keys import KeysError, check_keys
from frlg_minigames.profile import LdnConfig


def _say(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="frlg_minigames.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list USB serial ports, likely ESP32 boards first")
    p = sub.add_parser("prepare", help="write the radio firmware to the board (first time only)")
    p.add_argument("--port", required=True)
    for name in ("jump", "berry"):
        p = sub.add_parser(name, help={"jump": "Pokemon Jump, 2 players, the Switch leads",
                                       "berry": "Dodrio Berry Picking, 3 players, the PC leads"}[name])
        p.add_argument("--port", required=True, help="the board's port, e.g. COM7")
        p.add_argument("--keys", required=True, help="the Switch's prod.keys")
        p.add_argument("--log", help="write the detailed log here")
        p.add_argument("--capture", help="write the packet capture (.jsonl) here")
    args = ap.parse_args(argv)

    if args.cmd == "ports":
        for port in board.ports():
            print(port.label)
        return 0
    if args.cmd == "prepare":
        board.flash(args.port, say=_say)
        return 0

    try:
        check_keys(args.keys)
    except KeysError as exc:
        _say(str(exc))
        return 2
    os.environ["FRLG_RADIO"] = f"esp32:{args.port}"
    log_file = open(args.log, "a", encoding="utf-8", buffering=1) if args.log else None

    def log(*parts):
        if log_file:
            log_file.write(f"{time.strftime('%H:%M:%S')} {' '.join(str(p) for p in parts)}\n")
    log.info = log
    stop = threading.Event()
    ldn = LdnConfig(keys_path=args.keys, capture_path=args.capture)
    if args.cmd == "jump":
        from frlg_minigames.pokejump_app import PokeJumpApp
        app = PokeJumpApp(ldn=ldn, log=log, status=_say, stop=stop)
    else:
        from frlg_minigames.berrypick_app import BerryPickApp
        app = BerryPickApp(ldn=ldn, log=log, status=_say, stop=stop)
    try:
        app.run()
    except KeyboardInterrupt:
        stop.set()
        _say("Stopped.")
    finally:
        if log_file:
            log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
