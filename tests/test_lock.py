"""What ships is what was locked and tested.

The image installs deploy/requirements.txt (and the test stage requirements-dev.txt)
with --require-hashes. Both are exported from uv.lock, which the local test runs
use too. If they drift, the image would install versions nobody tested.
Refresh after changing dependencies:

    uv lock
    uv export --frozen --no-emit-project --extra server -o deploy/requirements.txt
    uv export --frozen --no-emit-project --extra server --extra dev -o deploy/requirements-dev.txt
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")


def _body(text: str) -> str:
    # The header comment records the command that wrote the file; compare the pins.
    return "\n".join(l for l in text.splitlines() if not l.startswith("#"))


@pytest.mark.skipif(UV is None, reason="uv is not installed (the CI image checks this in the build)")
@pytest.mark.parametrize("name,extras", [("requirements.txt", ["server"]),
                                         ("requirements-dev.txt", ["server", "dev"])])
def test_requirements_match_the_lockfile(name, extras):
    args = [UV, "export", "--frozen", "--no-emit-project", "--format", "requirements-txt"]
    for e in extras:
        args += ["--extra", e]
    want = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    have = (ROOT / "deploy" / name).read_text()
    assert _body(have) == _body(want), f"deploy/{name} is out of date with uv.lock; see this file's docstring"


def test_every_pin_has_a_hash():
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = (ROOT / "deploy" / name).read_text()
        pins = re.findall(r"^([A-Za-z0-9_.-]+)==", text, re.M)
        assert pins, name
        blocks = re.split(r"^(?=[A-Za-z0-9_.-]+==)", text, flags=re.M)[1:]
        assert all("--hash=sha256:" in b for b in blocks), name


def test_base_image_is_pinned_by_digest():
    froms = re.findall(r"^FROM\s+(\S+)", (ROOT / "deploy" / "Dockerfile").read_text(), re.M)
    external = [f for f in froms if f not in ("base",)]
    assert external and all("@sha256:" in f for f in external), froms
