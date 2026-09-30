"""Run the JavaScript renderer tests as part of the normal pytest run.

The markdown renderer lives in graph.js, so it needs a JS runtime to test
honestly. Skipping when node is absent is better than pretending the browser
code is covered by the Python suite.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SUITE = Path(__file__).parent / "test_markdown.mjs"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_markdown_renderer():
    p = subprocess.run([NODE, str(SUITE)], capture_output=True, text=True, timeout=120)
    print(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_graph_layout():
    suite = Path(__file__).parent / "test_layout.mjs"
    p = subprocess.run([NODE, str(suite)], capture_output=True, text=True, timeout=120)
    print(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_graph_folder_focus():
    suite = Path(__file__).parent / "test_focus.mjs"
    p = subprocess.run([NODE, str(suite)], capture_output=True, text=True, timeout=120)
    print(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr
