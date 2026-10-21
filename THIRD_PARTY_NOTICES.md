# Third-party notices

FRLG Minigame Partner is licensed under the AGPL-3.0 (LICENSE). It includes or bundles the following.

| Component | Where | License |
|---|---|---|
| PokeLDN (link layer, adapted) | `frlg_minigames/net/` | AGPL-3.0 — https://github.com/Decryptu/pokeldn |
| PokeLDN ESP32 radio firmware 1.7.0 (unmodified) | `firmware/esp32-radio.bin` | AGPL-3.0 — see `firmware/NOTICE.md` |
| LDN by Yannik Marchand (with PokeLDN's and our changes) | `third_party/LDN/` | GPL-3.0 — see `third_party/LDN/LICENSE`, `UPSTREAM.md` |
| Pretendard | `frlg_minigames/gui/web/fonts/` | SIL OFL 1.1 — see `fonts/OFL.txt` |

Python packages bundled in the Windows executable:

| Package | License |
|---|---|
| pywebview | BSD-3-Clause |
| pythonnet, clr_loader | MIT |
| bottle, proxy_tools | MIT |
| pyserial | BSD-3-Clause |
| esptool (and esp-pylib, reedsolo, intelhex, bitstring, ecdsa, rich, rich-click, click, PyYAML, cryptography) | GPL-2.0-or-later (esptool); the others MIT, BSD, Apache-2.0 or public domain |
| pycryptodome | BSD-2-Clause / public domain |
| trio, sniffio, outcome, attrs, sortedcontainers, idna, cffi, pycparser | MIT / Apache-2.0 / BSD |
| zstandard | BSD-3-Clause |
| python-netlink | GPL-3.0 |
| CPython | PSF License |

The game logic is ported from the pret/pokefirered decompilation (https://github.com/pret/pokefirered).
