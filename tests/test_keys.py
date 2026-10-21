"""check_keys: a prod.keys the link can use passes; a missing, empty, short or wrong one says why."""

import os

import pytest

from frlg_minigames.keys import RETAIL_SHA256, KeysError, check_keys

REAL_KEYS = os.path.expanduser(os.path.join("~", ".switch", "prod.keys"))


def write(tmp_path, text):
    path = tmp_path / "prod.keys"
    path.write_text(text)
    return str(path)


def fake_line(name):
    return f"{name} = {'00' * 16}\n"


def test_missing_file(tmp_path):
    with pytest.raises(KeysError, match="not found"):
        check_keys(str(tmp_path / "missing.keys"))


def test_file_without_keys(tmp_path):
    with pytest.raises(KeysError, match="no keys"):
        check_keys(write(tmp_path, "hello\n"))


def test_names_the_missing_keys(tmp_path):
    path = write(tmp_path, fake_line("master_key_00") + fake_line("aes_kek_generation_source"))
    with pytest.raises(KeysError, match="missing master_key_12, aes_key_generation_source"):
        check_keys(path)


def test_wrong_values(tmp_path):
    path = write(tmp_path, "".join(fake_line(name) for name in RETAIL_SHA256))
    with pytest.raises(KeysError, match="doesn't match a retail Switch"):
        check_keys(path)


@pytest.mark.skipif(not os.path.isfile(REAL_KEYS), reason="no ~/.switch/prod.keys on this machine")
def test_real_keys_pass():
    check_keys(REAL_KEYS)
