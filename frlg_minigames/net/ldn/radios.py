"""Radio selection: the ESP32 board named by FRLG_RADIO=esp32:PORT (or esp32:auto)."""
import os


def join_timeout(seconds):
    return seconds


def selected() -> str | None:
    """The scheme FRLG_RADIO names, or None."""
    spec = os.environ.get("FRLG_RADIO", "")
    return spec.split(":", 1)[0] if ":" in spec else None


def use_from_environment(log=print) -> None:
    """Installs the board FRLG_RADIO names before the first LDN call (ldn.wlan.create_factory)."""
    scheme = selected()
    if scheme is None:
        return
    if scheme != "esp32":
        raise RuntimeError(f"FRLG_RADIO={scheme}:... names no radio; use esp32:PORT or esp32:auto")
    from frlg_minigames.net.ldn.esp32_wlan import use_from_environment as esp32
    esp32(log=log)
