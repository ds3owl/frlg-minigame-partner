"""FRLG Minigame Partner: Pokemon Jump and Dodrio Berry Picking (Two Island Joyful Game Corner) on the
Switch release of FireRed and LeafGreen, with the PC filling the other players' places.

    net/        the Switch local-wireless link (LDN, Pia, the GBA wireless adapter, the link layer)
    pokejump_*  Pokemon Jump, the PC as a member of the Switch's group
    berrypick_* Dodrio Berry Picking, the PC as the group's leader
    gui/        the desktop app
"""

import os
import sys

__version__ = "1.0.0"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The bundled LDN library (third_party/LDN, GPL-3.0) is imported as `ldn`.
_LDN = os.path.join(getattr(sys, "_MEIPASS", REPO_ROOT), "third_party", "LDN")
if os.path.isdir(os.path.join(_LDN, "ldn")) and _LDN not in sys.path:
    sys.path.insert(0, _LDN)
