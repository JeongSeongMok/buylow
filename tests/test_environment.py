"""LEAN 런타임 환경 해석의 크로스플랫폼(특히 Windows) 분기 단위테스트.

실제 인터프리터/DLL 해석은 OS에 의존하므로, 순수 분기 함수(파일명·argv·venv 경로)를
플랫폼 인자/모킹으로 검증한다. 현재 OS에서의 실제 해석은 integration으로 따로 확인.
"""

import subprocess
import sys

import pytest

from orchestrator.lean import environment as env


# ── 플랫폼별 순수 매핑 ────────────────────────────────────────────────────────
def test_libpython_filename_per_platform():
    assert env._libpython_filename("darwin") == "libpython3.11.dylib"
    assert env._libpython_filename("linux") == "libpython3.11.so"
    assert env._libpython_filename("win32") == "python311.dll"


def test_libpython_filename_unknown_platform_raises():
    with pytest.raises(RuntimeError):
        env._libpython_filename("plan9")


def test_dotnet_exe_name_per_platform():
    assert env._dotnet_exe_name("win32") == "dotnet.exe"
    assert env._dotnet_exe_name("darwin") == "dotnet"
    assert env._dotnet_exe_name("linux") == "dotnet"


def test_venv_python_relpath_per_platform():
    # Windows는 Scripts\python.exe, 그 외 bin/python.
    assert env._venv_python_relpath("win32").parts == ("Scripts", "python.exe")
    assert env._venv_python_relpath("darwin").parts == ("bin", "python")
    assert env._venv_python_relpath("linux").parts == ("bin", "python")


# ── _find_python311: OS별 후보 순서 + 버전 검증 ───────────────────────────────
def _fake_run_version(ver: str):
    """argv -c ... 호출에 버전 문자열을 돌려주는 가짜 subprocess.run."""
    def run(argv, capture_output=False, text=False, check=False, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=ver + "\n", stderr="")
    return run


def test_find_python311_windows_prefers_py_launcher(monkeypatch):
    monkeypatch.setattr(env.sys, "platform", "win32")
    # py 런처만 존재한다고 가정.
    monkeypatch.setattr(env.shutil, "which",
                        lambda name: r"C:\Windows\py.exe" if name == "py" else None)
    monkeypatch.setattr(env.subprocess, "run", _fake_run_version("3.11"))
    argv = env._find_python311()
    # 'py -3.11' → 2토큰 argv로 반환되어야 한다(분봉/백테 호출이 [*argv, '-c', ...]로 펼침).
    assert argv == [r"C:\Windows\py.exe", "-3.11"]


def test_find_python311_unix_uses_python311(monkeypatch):
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setattr(env.shutil, "which",
                        lambda name: "/usr/bin/python3.11" if name == "python3.11" else None)
    monkeypatch.setattr(env.subprocess, "run", _fake_run_version("3.11"))
    assert env._find_python311() == ["/usr/bin/python3.11"]


def test_find_python311_skips_wrong_version(monkeypatch):
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setattr(env.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(env.subprocess, "run", _fake_run_version("3.12"))  # 전부 3.12
    with pytest.raises(RuntimeError, match="3.11"):
        env._find_python311()


def test_find_python311_error_hint_is_os_specific(monkeypatch):
    monkeypatch.setattr(env.sys, "platform", "win32")
    monkeypatch.setattr(env.shutil, "which", lambda name: None)  # 아무것도 없음
    with pytest.raises(RuntimeError, match="winget"):
        env._find_python311()


# ── 현재 OS에서의 실제 해석(integration) ──────────────────────────────────────
@pytest.mark.integration
def test_resolve_pythonnet_pydll_on_this_os():
    # 개발 머신(3.11 설치돼 있어야)에서 실제 libpython 경로가 잡히는지.
    pydll = env._resolve_pythonnet_pydll()
    assert pydll.exists()
    assert pydll.name == env._libpython_filename()
