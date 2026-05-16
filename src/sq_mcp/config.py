"""SQ X install detection and runtime config."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

_LINUX_DEFAULTS = (
    Path.home() / "Apps" / "StrategyQuantX",
    Path("/opt/StrategyQuantX"),
)
_MAC_DEFAULTS = (
    Path("/Applications/StrategyQuantX.app/Contents/Resources/app"),
    Path.home() / "Applications" / "StrategyQuantX",
)
_WIN_DEFAULTS = (
    Path("C:/Program Files/StrategyQuant X"),
    Path("C:/Program Files (x86)/StrategyQuant X"),
    Path.home() / "AppData" / "Local" / "Programs" / "StrategyQuant X",
)


def _platform_defaults() -> tuple[Path, ...]:
    s = platform.system()
    if s == "Linux":
        return _LINUX_DEFAULTS
    if s == "Darwin":
        return _MAC_DEFAULTS
    if s == "Windows":
        return _WIN_DEFAULTS
    return ()


def _sqcli_name() -> str:
    return "sqcli.exe" if platform.system() == "Windows" else "sqcli"


@dataclass(frozen=True, slots=True)
class SqConfig:
    sqx_home: Path
    sqcli: Path
    user_dir: Path
    data_dir: Path
    projects_dir: Path
    http_url: str
    http_port: int
    license_no_check: bool
    log_path: Path | None = None
    h2_jar: Path | None = None

    @property
    def history_dir(self) -> Path:
        return self.data_dir / "History"

    @property
    def is_valid(self) -> bool:
        return self.sqcli.exists() and self.user_dir.exists()


def detect_config() -> SqConfig:
    """Resolve SQ X install + runtime config from env vars and platform defaults.

    Honored env vars:
      SQX_HOME           — install root (overrides auto-detect)
      SQX_HTTP_URL       — base URL of HTTP API (default http://localhost:5050)
      SQX_HTTP_PORT      — port only, used for spawn (default 5050)
      SQX_LICENSE_NOCHECK — '1' / 'true' to use the *_nocheck launcher when present
    """
    home_env = os.environ.get("SQX_HOME")
    candidates: list[Path] = []
    if home_env:
        candidates.append(Path(home_env).expanduser())
    candidates.extend(_platform_defaults())

    sqcli_in_path = shutil.which(_sqcli_name())
    if sqcli_in_path:
        candidates.append(Path(sqcli_in_path).resolve().parent)

    chosen: Path | None = None
    for cand in candidates:
        if (cand / _sqcli_name()).exists():
            chosen = cand
            break
    if chosen is None:
        # last resort — return a config that .is_valid == False so tools can complain clearly
        chosen = candidates[0] if candidates else Path.home() / "Apps" / "StrategyQuantX"

    sqcli = chosen / _sqcli_name()
    user_dir = chosen / "user"

    log_env = os.environ.get("SQX_LOG_PATH")
    if log_env:
        log_candidate: Path | None = Path(log_env).expanduser()
    else:
        default_log = Path("/tmp/sqcli.log")
        log_candidate = default_log if default_log.exists() else None

    h2_jar_candidate = chosen / "internal" / "libs" / "h2.jar"
    h2_jar = h2_jar_candidate if h2_jar_candidate.exists() else None

    return SqConfig(
        sqx_home=chosen,
        sqcli=sqcli,
        user_dir=user_dir,
        data_dir=user_dir / "data",
        projects_dir=user_dir / "projects",
        http_url=os.environ.get("SQX_HTTP_URL", "http://localhost:5050"),
        http_port=int(os.environ.get("SQX_HTTP_PORT", "5050")),
        license_no_check=os.environ.get("SQX_LICENSE_NOCHECK", "1") in {"1", "true", "True"},
        log_path=log_candidate,
        h2_jar=h2_jar,
    )
