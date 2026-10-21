"""Checks a prod.keys file before a run: the four keys the wireless link derives its keys from must
be there and be a retail Switch's. Only SHA-256 digests of the retail values are kept here."""

import hashlib
import os

# The LDN key derivation (third_party/LDN/ldn KeyDerivation): master_key_00 for protocol 1,
# master_key_12 for protocol 3, and the two generation sources for both.
RETAIL_SHA256 = {
    "master_key_00": "0ee359be3c864bb0782e1d70a718a0342c551eed28c369754f9c4f691becf7ca",
    "master_key_12": "8c049dbea115d7c5dee618e0dcc4b95387462da30464dc11e28f6ee8184b033b",
    "aes_kek_generation_source": "fc02b9d37b42d7a1452e71444f1f700311d1132e301a83b16062e72a78175085",
    "aes_key_generation_source": "fbd10056999edc7acdb96098e47e2c3606230270d23281e671f0f389fc5bc585",
}


class KeysError(ValueError):
    """A sentence the player can act on."""


def read_keys(path):
    """`name = hex` lines -> {name: bytes}; blank lines, comments and unreadable values are skipped."""
    keys = {}
    with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            name, sep, value = line.partition("=")
            if not sep:
                continue
            try:
                keys[name.strip()] = bytes.fromhex(value.strip())
            except ValueError:
                continue
    return keys


def check_keys(path):
    """None when every key the link needs matches a retail Switch; KeysError otherwise."""
    if not path or not os.path.isfile(os.path.expanduser(path)):
        raise KeysError("prod.keys was not found.")
    try:
        keys = read_keys(path)
    except OSError:
        raise KeysError("Can't read this prod.keys file.") from None
    if not keys:
        raise KeysError("This file has no keys in it. Choose your prod.keys.")
    missing = [name for name in RETAIL_SHA256 if name not in keys]
    if missing:
        raise KeysError(f"This prod.keys is missing {', '.join(missing)}. "
                        "Dump the keys from your Switch again.")
    wrong = [name for name, digest in RETAIL_SHA256.items()
             if hashlib.sha256(keys[name]).hexdigest() != digest]
    if wrong:
        raise KeysError(f"{', '.join(wrong)} in this prod.keys doesn't match a retail Switch. "
                        "Dump the keys from your Switch again.")
