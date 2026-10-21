"""Who the PC is on the link, and the radio and hosting settings both minigames run with."""

from dataclasses import dataclass

from frlg_minigames.net.frlg.link import linkplayer
from frlg_minigames.net.frlg.text import charmap
from frlg_minigames.net.gba import ni

VERSIONS = {"firered": linkplayer.VERSION_FIRE_RED, "leafgreen": linkplayer.VERSION_LEAF_GREEN}
LANGUAGES = {
    "japanese": linkplayer.LANGUAGE_JAPANESE,
    "english": linkplayer.LANGUAGE_ENGLISH,
    "french": linkplayer.LANGUAGE_FRENCH,
    "italian": linkplayer.LANGUAGE_ITALIAN,
    "german": linkplayer.LANGUAGE_GERMAN,
    "spanish": linkplayer.LANGUAGE_SPANISH,
}

# The name the Switch shows for the PC, in the group list and on the minigame screens.
PC_NAME = "Ash"


@dataclass(frozen=True)
class TrainerProfile:
    name: str = PC_NAME
    tid: int = 0x8822
    sid: int = 0x47ED
    gender: int = 0
    version: str = "leafgreen"
    language: str = "english"
    has_national_dex: bool = True
    has_completed_game: bool = True

    def __post_init__(self):
        encoded = charmap.encode(self.name, language=self.language)
        if not self.name or charmap.decode(encoded, language=self.language) != self.name:
            raise ValueError("trainer name contains unsupported Gen III characters")
        if len(encoded) > (5 if self.language == "japanese" else 7):
            raise ValueError("trainer name is too long")
        if not (0 <= self.tid <= 0xFFFF and 0 <= self.sid <= 0xFFFF):
            raise ValueError("TID and SID must each fit in 16 bits")
        if self.version not in VERSIONS or self.language not in LANGUAGES:
            raise ValueError("unknown version or language")

    @property
    def trainer_id(self):
        return (self.sid << 16) | self.tid

    @property
    def progress_flags(self):
        return (1 if self.has_national_dex else 0) | (0x10 if self.has_completed_game else 0)

    @property
    def discovery_name(self):
        return self.name

    @property
    def discovery_trainer_id(self):
        return self.tid

    @property
    def session_name(self):
        return self.name

    @property
    def national_flags(self):
        """Set for Ruby, Sapphire and Emerald only; FireRed and LeafGreen advertise none."""
        return dict(national_link=False, national_dex=False, game_clear=False)

    def to_link_player(self):
        return linkplayer.LinkPlayer(
            name=self.name, trainer_id=self.trainer_id, version=VERSIONS[self.version],
            progress_flags=self.progress_flags, progress_flags_copy=self.progress_flags,
            gender=self.gender, player_id=0, language=LANGUAGES[self.language])

    def build_rfu_game_data(self, activity, *, started=True):
        return ni.build_game_data(VERSIONS[self.version], self.tid, self.name,
                                  language=LANGUAGES[self.language], activity=activity,
                                  started=started, **self.national_flags)


@dataclass(frozen=True)
class LdnConfig:
    keys_path: str = "~/.switch/prod.keys"
    password: bytes | None = None
    phy: str = "phy0"            # a Linux Wi-Fi card only; unused with the ESP32 board
    local_comm_id: int | None = None
    capture_path: str | None = None


@dataclass(frozen=True)
class HostOptions:
    """The leader's radio settings, the ones hardware runs used (2026-10-15)."""
    channel: int = 1
    scene_id: int | None = None
    max_participants: int = 6
    skip_preflight: bool = False
    skip_encryption: bool = True
    accept_decrypted_ccmp: bool = True
    native_nonce_sequence: bool = True
    session_response_first: bool = True
    protocol_tick_hz: float = 59.727      # one RFU slot per GBA VBlank
