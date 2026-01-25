"""MSVC environment and dependency detection utilities."""

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List

from ._repo_file import RepoFile, ValidatedRepoFile
from ._type_check import typecheck_methods


@typecheck_methods
class MsvcEnv:
    """Manages MSVC environment variables from vcvarsall.bat.

    Caches environment to disk to avoid repeated vcvarsall.bat calls.
    """

    _data_dir = Path.home() / ".quicken"
    _instance = None  # Singleton instance
    _env = None  # Cached environment

    @classmethod
    def get(cls) -> Dict[str, str]:
        """Get MSVC environment variables, loading lazily and caching."""
        if cls._env is None:
            cls._env = cls._load_environment()
        return cls._env

    @classmethod
    def get_config(cls) -> Dict:
        """Load configuration from tools.json."""
        with open(cls._data_dir / "tools.json", 'r', encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def _load_environment(cls) -> Dict[str, str]:
        """Load MSVC environment, using disk cache if available."""
        config = cls.get_config()
        vcvarsall = config["vcvarsall"]
        msvc_arch = config.get("msvc_arch", "x64")

        cache_file = cls._data_dir / "msvc_env.json"

        # Try to load from cache
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if (cached_data.get("vcvarsall") == vcvarsall and
                        cached_data.get("msvc_arch") == msvc_arch):
                        return cached_data.get("env", {})
            except (json.JSONDecodeError, KeyError):
                pass

        # Run vcvarsall and capture environment
        cmd = f'"{vcvarsall}" {msvc_arch} >nul && set'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse environment variables from output
        env = os.environ.copy()
        for line in result.stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                env[key] = value

        # Save to cache
        cache_data = {
            "vcvarsall": vcvarsall,
            "msvc_arch": msvc_arch,
            "env": env
        }

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w', encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            pass

        return env


def get_dependencies_showincludes(main_file: Path, repo_dir: Path) -> List[RepoFile]:
    """Get C++ file dependencies using MSVC /showIncludes.

    Args:    main_file: Absolute path to source file
             repo_dir: Repository root directory
    Returns: List of RepoFile instances for all dependencies (including main_file)
    """
    config = MsvcEnv.get_config()
    cl_path = config["cl"]

    result = subprocess.run(
        [cl_path, '/showIncludes', '/Zs', str(main_file)],
        env=MsvcEnv.get(),
        capture_output=True,
        text=True,
        check=False
    )

    dependencies = [ValidatedRepoFile(repo_dir, main_file)]

    for line in result.stderr.splitlines():
        if line.startswith("Note: including file:"):
            file_path_str = line.split(":", 2)[2].strip()
            try:
                repo_file = ValidatedRepoFile(repo_dir, Path(file_path_str))
                dependencies.append(repo_file)
            except ValueError:
                pass  # Skip dependencies outside repo

    return dependencies
