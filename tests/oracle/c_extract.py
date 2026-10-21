"""Cut definitions out of the pret decompilation verbatim, for differential tests that run the
original C next to the Python port (tests/test_frlg_minigame_oracle.py)."""

import os
import re
import shutil
import subprocess
import tempfile


def decomp_src():
    """pret/pokefirered's src/, from POKEFIRERED_SRC or a sibling checkout; None if absent."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.environ.get("POKEFIRERED_SRC"),
                 os.path.join(here, "..", "..", "..", "pokefirered", "src")):
        if path and os.path.isfile(os.path.join(path, "pokemon_jump.c")):
            return os.path.abspath(path)
    return None


def function(source, name):
    """The full text of `static ... name(...)` up to its closing brace at column 0."""
    m = re.search(r"^(static [^\n;]*\b" + re.escape(name) + r"\([^;{]*\)\s*\n\{)", source, re.M)
    if m is None:
        raise KeyError(name)
    end = source.index("\n}\n", m.start()) + 3
    return source[m.start():end]


def block(source, start_pattern):
    """From a line matching start_pattern to the next `};` at column 0."""
    m = re.search(start_pattern, source, re.M)
    if m is None:
        raise KeyError(start_pattern)
    line_end = source.index("\n", m.start())
    if source[m.start():line_end].rstrip().endswith("};"):
        return source[m.start():line_end]
    end = source.index("\n};", m.start()) + 3
    return source[m.start():end]


def lines(source, first, last):
    return "\n".join(source.split("\n")[first - 1:last])


def compile_and_run(code, stdin_text):
    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None:
        raise RuntimeError("no C compiler")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "oracle.c")
        exe = os.path.join(tmp, "oracle")
        with open(src, "w") as f:
            f.write(code)
        subprocess.run([cc, "-O1", "-w", "-fno-strict-aliasing", "-o", exe, src], check=True)
        out = subprocess.run([exe], input=stdin_text, capture_output=True, text=True, check=True)
        return out.stdout


def have_compiler():
    return shutil.which("gcc") is not None or shutil.which("cc") is not None
