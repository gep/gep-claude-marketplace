import json
import os
import subprocess

import pytest

from dg_testkit import DG
from dg_testkit.envs import IsolatedEnv
from dg_testkit.pythons import Pythons
from dg_testkit.stubs import EntryStubs

STUBS = EntryStubs.stubs_of(DG)


@pytest.mark.parametrize("stub", STUBS, ids=EntryStubs.stub_id)
def test_stub_parses_as_python_37(stub):
    EntryStubs.check_parses_as_37(stub[0])


@pytest.mark.parametrize("stub", STUBS, ids=EntryStubs.stub_id)
def test_old_python_gets_exit_3(old_python, stub):
    EntryStubs.check_old_python_exit_3(old_python, stub[0])


@pytest.mark.parametrize(("stub", "args", "stdout"), [("gate_main.py", [], ""), ("session_main.py", [], ""), ("prompt_main.py", [], ""), ("check.py", ["--json", "--config"], None)])
def test_314_imports_real_package(tmp_path, stub, args, stdout):
    # P8: the stubs hand over to the real package, not to a module planted next to the cwd.
    (tmp_path / "destructive_guard.py").write_text("raise SystemExit(99)\n")
    python = Pythons.require_new_python(os.environ)
    env = IsolatedEnv.clean({"GATE_ENABLED": "0"} if stub != "check.py" else {})
    proc = subprocess.run(
        [python, "-I", str(DG / "lib" / stub), *args], capture_output=True, text=True, cwd=tmp_path, env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    if stdout is None:
        assert "backends" in json.loads(proc.stdout)
    else:
        assert proc.stdout == stdout
