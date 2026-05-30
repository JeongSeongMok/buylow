"""LEAN 프로세스를 띄우기 위한 런타임 환경 해석.

run-backtest.sh가 셸로 하던 일(.NET·Python3.11·venv·AlgorithmImports 경로 해석, 런처 빌드)을
오케스트레이터가 코드로 흡수한 것. 토스/실거래와 무관하며 백테스트·라이브 spawn에 공통으로 쓰인다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# net10 호환 LEAN NuGet 계보. semver상 더 큰 10730.x는 net462라 금지 (docs/DEVELOPMENT.md).
LEAN_PKG_VERSION = "2.5.17757"

# orchestrator/lean/environment.py → parents[2] = repo 루트
REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_CSPROJ = REPO_ROOT / "launcher" / "BuylowLauncher.csproj"
LAUNCHER_OUT = REPO_ROOT / "launcher" / "bin" / "Release" / "net10.0"
LEANPY_DIR = REPO_ROOT / ".leanpy"


@dataclass(frozen=True)
class LeanEnvironment:
    """LEAN 프로세스 spawn에 필요한, 해석이 끝난 경로 묶음."""

    dotnet_exe: Path
    dotnet_root: Path
    pythonnet_pydll: Path        # pythonnet이 로드할 libpython (PYTHONNET_PYDLL)
    venv_site_packages: Path     # pandas/numpy 깐 3.11 venv의 site-packages
    algorithm_imports_dir: Path  # 'from AlgorithmImports import *' 해소용 디렉토리
    launcher_dll: Path           # 빌드된 BuylowLauncher.dll

    def process_env(self, pythonpath_parts: list[str]) -> dict[str, str]:
        """LEAN 프로세스에 넘길 환경변수(os.environ + .NET/pythonnet 설정)."""
        env = dict(os.environ)
        env["DOTNET_ROOT"] = str(self.dotnet_root)
        env["PATH"] = f"{self.dotnet_root}{os.pathsep}{env.get('PATH', '')}"
        env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
        env["PYTHONNET_PYDLL"] = str(self.pythonnet_pydll)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
        return env


def _libpython_filename() -> str:
    """플랫폼별 libpython 3.11 공유 라이브러리 파일명."""
    if sys.platform == "darwin":
        return "libpython3.11.dylib"
    if sys.platform.startswith("linux"):
        return "libpython3.11.so"
    # Windows 등은 아직 미검증 — 개발 환경(macOS) 외 지원은 추후 추가.
    raise RuntimeError(f"지원하지 않는 플랫폼: {sys.platform} (현재 macOS/Linux만 지원)")


def _resolve_dotnet() -> tuple[Path, Path]:
    """(dotnet 실행파일, DOTNET_ROOT)를 해석. 기본 위치는 ~/.dotnet."""
    dotnet_root = Path(os.environ.get("DOTNET_ROOT", Path.home() / ".dotnet"))
    candidate = dotnet_root / "dotnet"
    if candidate.exists():
        return candidate, dotnet_root
    # PATH에 있으면 그걸 사용
    on_path = shutil.which("dotnet")
    if on_path:
        exe = Path(on_path)
        return exe, exe.parent
    raise RuntimeError("dotnet을 찾을 수 없음. .NET 10 SDK 설치 필요 (docs/DEVELOPMENT.md)")


def _resolve_pythonnet_pydll() -> Path:
    """LEAN pythonnet이 로드할 Python 3.11 공유 라이브러리 경로."""
    py311 = shutil.which("python3.11")
    if not py311:
        raise RuntimeError("python3.11을 찾을 수 없음 (예: 'brew install python@3.11')")
    libdir = subprocess.run(
        [py311, "-c", "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    pydll = Path(libdir) / _libpython_filename()
    if not pydll.exists():
        raise RuntimeError(f"libpython3.11을 찾을 수 없음: {pydll}")
    return pydll


def _ensure_leanpy_venv() -> Path:
    """LEAN Python 연동에 필요한 pandas/numpy를 담은 3.11 venv를 보장하고 site-packages 반환."""
    venv_python = LEANPY_DIR / "bin" / "python"
    if not venv_python.exists():
        if not shutil.which("uv"):
            raise RuntimeError("uv를 찾을 수 없음 (https://github.com/astral-sh/uv)")
        print(f">> LEAN Python 런타임 venv 생성 ({LEANPY_DIR})")
        subprocess.run(["uv", "venv", "--python", "3.11", str(LEANPY_DIR)], check=True)
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python), "pandas", "numpy"],
            check=True,
        )
    site_packages = subprocess.run(
        [str(venv_python), "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(site_packages)


def _build_launcher(dotnet_exe: Path, dotnet_root: Path) -> Path:
    """thin 런처를 빌드(=NuGet 복원 포함)하고 산출 DLL 경로 반환."""
    env = dict(os.environ)
    env["DOTNET_ROOT"] = str(dotnet_root)
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    print(">> 런처 빌드")
    subprocess.run(
        [str(dotnet_exe), "build", str(LAUNCHER_CSPROJ), "-c", "Release", "--nologo", "-v", "quiet"],
        check=True, env=env,
    )
    dll = LAUNCHER_OUT / "BuylowLauncher.dll"
    if not dll.exists():
        raise RuntimeError(f"빌드 후 런처 DLL이 없음: {dll}")
    return dll


def _resolve_algorithm_imports() -> Path:
    """AlgorithmImports.py가 든 디렉토리(QuantConnect.Common NuGet의 content/)."""
    ai_dir = (
        Path.home() / ".nuget" / "packages" / "quantconnect.common"
        / LEAN_PKG_VERSION / "content"
    )
    if not (ai_dir / "AlgorithmImports.py").exists():
        raise RuntimeError(f"AlgorithmImports.py를 찾을 수 없음: {ai_dir} (런처 빌드 필요)")
    return ai_dir


def prepare_environment() -> LeanEnvironment:
    """LEAN 실행에 필요한 모든 경로를 해석/준비한다.

    순서 주의: 런처 빌드가 NuGet을 복원하므로, AlgorithmImports 해석은 빌드 이후에 한다.
    """
    dotnet_exe, dotnet_root = _resolve_dotnet()
    pydll = _resolve_pythonnet_pydll()
    site_packages = _ensure_leanpy_venv()
    launcher_dll = _build_launcher(dotnet_exe, dotnet_root)
    ai_dir = _resolve_algorithm_imports()
    return LeanEnvironment(
        dotnet_exe=dotnet_exe,
        dotnet_root=dotnet_root,
        pythonnet_pydll=pydll,
        venv_site_packages=site_packages,
        algorithm_imports_dir=ai_dir,
        launcher_dll=launcher_dll,
    )
