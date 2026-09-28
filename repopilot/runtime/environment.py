"""Detect only runtime facts available from this process."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


def _windows_parent_name() -> str | None:
    """Find the parent executable with Windows Toolhelp, without extra packages."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32FirstW.restype = wintypes.BOOL
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            processes: dict[int, tuple[int, str]] = {}
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                processes[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            parent_id = processes.get(os.getpid(), (0, ""))[0]
            return processes.get(parent_id, (0, ""))[1] or None
        finally:
            kernel.CloseHandle(snapshot)
    except (OSError, ValueError, AttributeError):
        return None


def _shell_name() -> str:
    override = os.getenv("REPOPILOT_SHELL")
    if override:
        return override
    if os.getenv("WSL_INTEROP"):
        return "WSL (shell unknown)"
    if os.getenv("MSYSTEM"):
        return "Git Bash"
    # The parent process is the strongest available signal.
    windows_parent = _windows_parent_name()
    if windows_parent:
        name = windows_parent.lower()
        if "pwsh" in name or "powershell" in name:
            return "PowerShell"
        if name == "cmd.exe":
            return "cmd"
        if "bash" in name:
            return "bash"
    # psutil also handles macOS/Linux and nested parent-process cases.
    try:
        import psutil  # type: ignore[import-not-found]

        parent = psutil.Process().parent()
        if parent:
            name = parent.name().lower()
            for marker, label in (
                ("pwsh", "PowerShell"),
                ("powershell", "PowerShell"),
                ("cmd.exe", "cmd"),
                ("bash", "bash"),
                ("zsh", "zsh"),
                ("fish", "fish"),
            ):
                if marker in name:
                    return label
            if name == "sh" or name == "sh.exe":
                return "sh"
    except (ImportError, OSError, AttributeError):
        pass
    shell = os.getenv("SHELL")
    if shell:
        name = Path(shell).name.lower()
        for marker in ("bash", "zsh", "fish", "sh"):
            if name in (marker, marker + ".exe"):
                return marker
    return "unknown"


@dataclass(frozen=True)
class RuntimeContext:
    os_name: str
    os_version: str
    shell: str
    cwd: str
    path_separator: str
    python_version: str
    git_available: bool
    ripgrep_available: bool

    @classmethod
    def detect(cls, workspace: Path | None = None) -> "RuntimeContext":
        return cls(
            os_name=platform.system(),
            os_version=platform.version(),
            shell=_shell_name(),
            cwd=str((workspace or Path.cwd()).resolve()),
            path_separator=os.sep,
            python_version=platform.python_version(),
            git_available=shutil.which("git") is not None,
            ripgrep_available=shutil.which("rg") is not None,
        )

    def prompt(self) -> str:
        return "\n".join(
            [
                "Runtime Environment",
                f"OS: {self.os_name} {self.os_version}",
                f"Shell: {self.shell}",
                f"Working Directory: {self.cwd}",
                f"Path Separator: {self.path_separator}",
                f"Python: {self.python_version}",
                f"Git: {'available' if self.git_available else 'unavailable'}",
                f"ripgrep: {'available' if self.ripgrep_available else 'unavailable'}",
                "Use path syntax compatible with this runtime. Do not assume bash unless detected.",
            ]
        )
