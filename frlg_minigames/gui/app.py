"""The desktop app: one window, the steps to follow, Start and Stop.

The page (web/index.html) polls Api.state() and calls the other Api methods; the link runs on a
worker thread. Settings and run logs live in %LOCALAPPDATA%/FRLG Minigame Partner."""

import glob
import json
import os
import pathlib
import sys
import threading
import time
import traceback

from frlg_minigames import REPO_ROOT, __version__, board
from frlg_minigames.keys import KeysError, check_keys
from frlg_minigames.profile import LdnConfig, PC_NAME

APP_NAME = "FRLG Minigame Partner"
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME)
LOG_DIR = os.path.join(DATA_DIR, "logs")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
KEEP_RUNS = 10
WEB = os.path.join(getattr(sys, "_MEIPASS", REPO_ROOT), "frlg_minigames", "gui", "web")

JUMP, BERRY = "jump", "berry"


class RunLog:
    """Timestamped lines into one run's log file; `info` is the same call (the link layer's API)."""

    def __init__(self, path):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self.info = self

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        with self._lock:
            if not self._fh.closed:
                self._fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")

    def write(self, text):          # stdout/stderr of the run's libraries
        text = text.rstrip("\n")
        if text:
            self(text)

    def flush(self):
        pass

    def close(self):
        with self._lock:
            self._fh.close()


def friendly(exc):
    """A sentence the player can act on, for an error that ended a run."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, FileNotFoundError) and "prod.keys" in text:
        return "prod.keys was not found. Choose the file in step 3."
    if "board speaks protocol" in low or "no reply 0x" in low:
        return "The board needs its firmware. Click Prepare board, then Start again."
    if "could not open port" in low or "permissionerror" in low or "access is denied" in low:
        return "Can't open the board's port. Check the cable, and close other programs using it."
    if "exactly one usb serial port" in low:
        return "Choose the board's port in step 1."
    if "the board left usb" in low or "stopped answering" in low or "usb connection failed" in low:
        return "The board disconnected. Unplug it, plug it back in, and Start again."
    return f"Stopped: {text.splitlines()[0] if text else type(exc).__name__}"


class Api:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self._window = None
        self._settings = {"game": JUMP, "port": "", "keys": "", "prepared": []}
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                self._settings.update(json.load(fh))
        except (OSError, ValueError):
            pass
        if not self._settings["keys"]:
            default = os.path.expanduser(os.path.join("~", ".switch", "prod.keys"))
            if os.path.isfile(default):
                self._settings["keys"] = default
        self._ports = []
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._app = None
        self._busy = ""              # "run" or "flash" while a worker holds the board
        self._status = ""
        self._error = ""
        self._joined = False
        self._playing = False
        self._log_path = ""
        self._keys_checked = None    # (path, mtime, error or "") of the last check_keys
        self.refresh_ports()

    # --- settings ---------------------------------------------------------------------------
    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump(self._settings, fh, indent=2)
        except OSError:
            pass

    def set_game(self, game):
        if not self._busy and game in (JUMP, BERRY):
            self._settings["game"] = game
            self._status = self._error = ""
            self._save()
        return self.state()

    def set_port(self, port):
        if not self._busy:
            self._settings["port"] = port
            self._save()
        return self.state()

    def refresh_ports(self):
        try:
            self._ports = board.ports()
        except Exception:                       # no serial backend: leave the list empty
            self._ports = []
        devices = [p.device for p in self._ports]
        if self._settings["port"] not in devices:
            likely = [p.device for p in self._ports if p.likely_board]
            self._settings["port"] = (likely or devices or [""])[0]
        return self.state()

    def choose_keys(self):
        if self._window is None or self._busy:
            return self.state()
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        picked = self._window.create_file_dialog(
            dialog, allow_multiple=False,
            file_types=("Key files (*.keys)", "All files (*.*)"))
        if picked:
            self._settings["keys"] = picked[0]
            self._save()
            self._error = self._keys_error()
        return self.state()

    def open_logs(self):
        os.startfile(LOG_DIR)       # Windows
        return self.state()

    def _keys_error(self):
        """check_keys' sentence for the chosen file ("" when it's good), rechecked when it changes."""
        path = self._settings["keys"] or ""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if self._keys_checked is None or self._keys_checked[:2] != (path, mtime):
            if mtime is None:
                self._keys_checked = (path, mtime, "Choose your prod.keys file in step 3.")
                return self._keys_checked[2]
            try:
                check_keys(path)
                error = ""
            except KeysError as exc:
                error = str(exc)
            self._keys_checked = (path, mtime, error)
        return self._keys_checked[2]

    # --- state ------------------------------------------------------------------------------
    def state(self):
        app = self._app
        streak = getattr(app, "streak", 0) if self._busy == "run" else 0
        connected_at = getattr(app, "connected_at", None)
        return {
            "version": __version__,
            "pc_name": PC_NAME,
            "game": self._settings["game"],
            "ports": [{"device": p.device, "label": p.label} for p in self._ports],
            "port": self._settings["port"],
            "keys": self._settings["keys"],
            "keys_ok": not self._keys_error(),
            "prepared": self._settings["port"] in self._settings["prepared"],
            "busy": self._busy,
            "status": self._status,
            "error": self._error,
            "joined": self._joined,
            "playing": self._playing,
            "streak": streak,
            "connected_for": int(time.monotonic() - connected_at) if connected_at and self._busy else 0,
        }

    # --- board ------------------------------------------------------------------------------
    def prepare_board(self):
        with self._lock:
            if self._busy or not self._settings["port"]:
                return self.state()
            self._busy, self._error = "flash", ""
            self._status = "Preparing the board. Don't unplug it..."
        port = self._settings["port"]
        threading.Thread(target=self._flash, args=(port,), daemon=True).start()
        return self.state()

    def _flash(self, port):
        log = self._open_log("board")
        try:
            from frlg_minigames.net.ldn import esp32_wlan
            esp32_wlan.release()
            out = board.flash(port, say=self._say_status(log))
            log(out)
            if port not in self._settings["prepared"]:
                self._settings["prepared"].append(port)
            self._save()
        except Exception as exc:  # noqa: BLE001 - shown to the player
            log(traceback.format_exc())
            text = str(exc)
            self._error = (text if text.startswith("This is an") else
                          "Couldn't prepare the board. Check the port and the cable, then try again.")
            self._status = ""
        finally:
            log.close()
            self._busy = ""

    # --- run --------------------------------------------------------------------------------
    def _open_log(self, kind):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._log_path = os.path.join(LOG_DIR, f"{stamp}-{kind}.log")
        old = sorted(glob.glob(os.path.join(LOG_DIR, "*")))
        for path in old[:-KEEP_RUNS * 2]:
            try:
                os.remove(path)
            except OSError:
                pass
        return RunLog(self._log_path)

    def _say_status(self, log):
        def say(text):
            log(f"[status] {text}")
            self._status = text
            if "joined" in text:
                self._joined = True
            if text in ("Jumping.", "Berries are falling."):
                self._playing = True
            if "left" in text or "closed" in text or text.startswith("Looking"):
                self._joined = self._playing = False
        return say

    def start(self):
        with self._lock:
            if self._busy:
                return self.state()
            if not self._settings["port"]:
                self._error = "Choose the board's port in step 1."
                return self.state()
            keys_error = self._keys_error()
            if keys_error:
                self._error = keys_error
                return self.state()
            self._busy, self._error, self._status = "run", "", "Starting..."
            self._joined = self._playing = False
            self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.state()

    def stop(self):
        self._stop_event.set()
        if self._busy == "run":
            self._status = "Stopping..."
        return self.state()

    def _run(self):
        game = self._settings["game"]
        log = self._open_log(game)
        say = self._say_status(log)
        capture = self._log_path[:-4] + ".jsonl"
        os.environ["FRLG_RADIO"] = f"esp32:{self._settings['port']}"
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        from frlg_minigames.net.ldn import esp32_wlan
        esp32_wlan.lost_hook = self._stop_event.set
        ldn = LdnConfig(keys_path=self._settings["keys"], capture_path=capture)
        try:
            log(f"[app] {APP_NAME} {__version__}, {game}, port {self._settings['port']}")
            if game == JUMP:
                from frlg_minigames.pokejump_app import PokeJumpApp
                self._app = PokeJumpApp(ldn=ldn, log=log, status=say, stop=self._stop_event)
            else:
                from frlg_minigames.berrypick_app import BerryPickApp
                self._app = BerryPickApp(ldn=ldn, log=log, status=say, stop=self._stop_event)
            self._app.run()
            from frlg_minigames.net.ldn import radio_lost
            lost = radio_lost()
            if lost:
                self._error = friendly(RuntimeError(lost))
            if not self._error:
                self._status = "Stopped." if self._stop_event.is_set() else (self._status or "Finished.")
        except BaseException as exc:  # noqa: BLE001 - every end of a run is shown
            log(traceback.format_exc())
            self._error = friendly(exc)
            self._status = ""
        finally:
            esp32_wlan.lost_hook = None
            try:
                esp32_wlan.release()
            except Exception:  # noqa: BLE001
                log(traceback.format_exc())
            sys.stdout, sys.stderr = old_out, old_err
            log.close()
            self._joined = self._playing = False
            self._busy = ""


def main():
    import webview
    api = Api()
    # A file:// URL: pywebview's own local HTTP server did not load the page on Windows.
    window = webview.create_window(
        APP_NAME, url=pathlib.Path(WEB, "index.html").as_uri(), js_api=api,
        width=560, height=800, min_size=(480, 640), background_color="#fbfbfd")
    api._window = window

    def closing():
        api._stop_event.set()
    window.events.closing += closing
    selftest = os.environ.get("FRLG_SELFTEST")
    webview.start((lambda: _selftest(window, selftest)) if selftest else None)


def _selftest(window, out_path):
    """FRLG_SELFTEST=FILE: load the page, import every runtime module, write the result, quit.
    Touches no board; used to check a frozen build."""
    result = {}
    try:
        result["loaded"] = window.events.loaded.wait(30)
        time.sleep(2)
        result["status"] = window.evaluate_js("document.getElementById('status').textContent")
        result["font"] = window.evaluate_js("document.fonts.check('14px Pretendard')")
        import esptool, ldn, serial.tools.list_ports  # noqa: F401,E401
        from frlg_minigames import berrypick_app, board as boardmod, pokejump_app  # noqa: F401
        from frlg_minigames.net.ldn import esp32_wlan, transport  # noqa: F401
        result["firmware"] = os.path.getsize(boardmod.FIRMWARE)
        result["ldn"] = ldn.__file__
        result["ok"] = True
    except Exception:  # noqa: BLE001
        result["error"] = traceback.format_exc()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    window.destroy()


if __name__ == "__main__":
    main()
