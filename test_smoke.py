"""Smoke-Tests: Startpunkte der CLI-Skripte via subprocess."""

import subprocess
import sys

PYTHON = sys.executable


def run(*args, **kwargs):
    return subprocess.run([PYTHON, *args], capture_output=True, text=True, **kwargs)


class TestCutSmoke:
    def test_version(self):
        result = run("cut.py", "--version")
        assert result.returncode == 0
        assert "cut.py" in result.stdout
        assert "1.9" in result.stdout

    def test_help(self):
        result = run("cut.py", "--help")
        assert result.returncode == 0
        assert "--no-songtext" in result.stdout
        assert "--out" in result.stdout
        assert "--preview" in result.stdout

    def test_no_args_exits_nonzero(self):
        result = run("cut.py")
        assert result.returncode != 0


class TestAssembleSmoke:
    def test_version(self):
        result = run("assemble.py", "--version")
        assert result.returncode == 0
        assert "assemble.py" in result.stdout

    def test_help(self):
        result = run("assemble.py", "--help")
        assert result.returncode == 0
        assert "--preview" in result.stdout

    def test_no_args_exits_nonzero(self):
        result = run("assemble.py")
        assert result.returncode != 0


class TestFetchSongtextSmoke:
    def test_no_args_exits_nonzero(self):
        result = run("fetch_songtext.py")
        assert result.returncode != 0
