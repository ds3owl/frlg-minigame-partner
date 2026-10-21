"""LDN calls; constants and parameter types come from the bundled library (third_party/LDN)."""
import ldn


def __getattr__(name):
    return getattr(ldn, name)
