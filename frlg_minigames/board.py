"""The ESP32 board: find its USB serial port and write the radio firmware (firmware/NOTICE.md)."""

import contextlib
import io
import os
import struct
import sys
from dataclasses import dataclass

from frlg_minigames import REPO_ROOT

FIRMWARE = os.path.join(getattr(sys, "_MEIPASS", REPO_ROOT), "firmware", "esp32-radio.bin")
FIRMWARE_VERSION = "1.7.0"

# USB-serial bridges ESP32 boards carry, by USB vendor id.
BRIDGES = {0x10C4: "CP210x", 0x1A86: "CH340", 0x0403: "FTDI", 0x303A: "ESP32 USB"}


@dataclass(frozen=True)
class Port:
    device: str
    label: str
    likely_board: bool


def ports():
    """USB serial ports, the likely ESP32 boards first."""
    from serial.tools import list_ports
    found = []
    for p in list_ports.comports():
        if p.vid is None:
            continue
        bridge = BRIDGES.get(p.vid)
        label = f"{p.device} - {bridge}" if bridge else f"{p.device} - {p.description}"
        found.append(Port(p.device, label, bridge is not None))
    return sorted(found, key=lambda p: (not p.likely_board, p.device))


def flash(port, say=print):
    """Detect a classic ESP32 on `port`, check the image and write it at 0x0. Raises on failure."""
    import esptool
    from esptool.bin_image import LoadFirmwareImage

    if not os.path.isfile(FIRMWARE):
        raise RuntimeError(f"firmware missing: {FIRMWARE}")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        chip = esptool.detect_chip(port)
    with chip:
        if chip.CHIP_NAME != "ESP32":
            raise RuntimeError(f"This is an {chip.CHIP_NAME}; the firmware is for a classic ESP32.")
        with open(FIRMWARE, "rb") as source:
            source.seek(chip.BOOTLOADER_FLASH_OFFSET)
            data = source.read()
        if len(data) < 24 or data[0] != 0xE9 or int.from_bytes(data[12:14], "little") != chip.IMAGE_CHIP_ID:
            raise RuntimeError("the firmware image does not match this chip")
        try:
            image = LoadFirmwareImage(chip.CHIP_NAME, data)
        except (struct.error, RuntimeError, TypeError) as error:
            raise RuntimeError("the firmware image is damaged") from error
        image.verify()
        say(f"Writing the radio firmware {FIRMWARE_VERSION} to the {chip.CHIP_NAME} on {port}...")
        with contextlib.redirect_stdout(out):
            esptool.main(["--baud", "460800", "--after", "hard-reset", "write-flash",
                          "0x0", os.path.abspath(FIRMWARE)], esp=chip)
    say("The board is ready.")
    return out.getvalue()
