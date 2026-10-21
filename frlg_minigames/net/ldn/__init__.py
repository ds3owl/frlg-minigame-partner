"""The wireless layer, independent of any game: LDN association, Pia, and the transport crypto."""

_radio_lost = ""


def radio_lost() -> str:
    """Why the radio left this process, or "": tells a lost board from the player's Stop."""
    return _radio_lost
