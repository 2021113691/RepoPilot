import os
import platform
from pathlib import Path

from repopilot.runtime.environment import RuntimeContext


def test_runtime_detection(tmp_path):
    runtime = RuntimeContext.detect(tmp_path)
    assert runtime.cwd == str(tmp_path.resolve())
    assert runtime.python_version == platform.python_version()
    assert isinstance(runtime.git_available, bool)
    assert isinstance(runtime.ripgrep_available, bool)
    assert runtime.shell
    assert "Runtime Environment" in runtime.prompt()
