################################################################################
# Copyright IBM Corporation 2024
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Go analysis backend that wraps the ``codeanalyzer-go`` binary.

This module provides :class:`GoCodeanalyzer`, the subprocess driver for Go static
analysis. It shells out to the ``codeanalyzer-go`` native binary (analogous to
Java shelling out to the ``codeanalyzer-*.jar``), reads the produced
``analysis.json``, and deserializes it into a :class:`~cldk.models.go.GoApplication`.

The binary must be on ``PATH`` or provided via ``analysis_backend_path``.
"""

import json
import logging
import shlex
import subprocess
from importlib import resources
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, List, Optional, Union

from cldk.analysis import AnalysisLevel
from cldk.models.go.models import GoApplication, GoCallable, GoFile, GoType
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)

_BINARY_NAME = "codeanalyzer-go"


class GoCodeanalyzer:
    """Subprocess driver for the ``codeanalyzer-go`` native binary.

    Args:
        project_dir: Path to the root of the Go project (must contain ``go.mod``).
        analysis_backend_path: Directory containing the ``codeanalyzer-go`` binary.
            When ``None``, the binary must be on ``PATH``.
        analysis_json_path: Directory where ``analysis.json`` should be written.
            When ``None``, the binary is invoked with ``--output`` pointing at a
            temporary directory and results are read from there.
        analysis_level: ``"symbol_table"`` (level 1) or ``"call_graph"`` (level 2).
        eager_analysis: When ``True``, always re-run the binary even if a cached
            ``analysis.json`` already exists.
        cache_dir: Optional path passed to the binary's ``--cache`` flag.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_backend_path: Union[str, Path, None],
        analysis_json_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        cache_dir: Union[str, Path, None] = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.analysis_backend_path = Path(analysis_backend_path) if analysis_backend_path else None
        self.analysis_json_path = Path(analysis_json_path) if analysis_json_path else None
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.application: GoApplication = self._init_codeanalyzer()

    # ── Binary resolution ──────────────────────────────────────────────────────

    def _get_codeanalyzer_exec(self) -> List[str]:
        """Return the shell argv list for the codeanalyzer-go binary."""
        if self.analysis_backend_path:
            binary = self.analysis_backend_path / _BINARY_NAME
            if not binary.exists():
                # Try without extension and with .exe on Windows.
                binary_exe = self.analysis_backend_path / f"{_BINARY_NAME}.exe"
                if binary_exe.exists():
                    binary = binary_exe
                else:
                    raise CodeanalyzerExecutionException(
                        f"codeanalyzer-go binary not found in {self.analysis_backend_path}"
                    )
            return [str(binary)]

        # Fall back to PATH.
        import shutil
        found = shutil.which(_BINARY_NAME)
        if found is None:
            raise CodeanalyzerExecutionException(
                f"'{_BINARY_NAME}' not found on PATH and no analysis_backend_path provided. "
                "Install codeanalyzer-go or pass analysis_backend_path."
            )
        return [found]

    # ── Init / cache check ─────────────────────────────────────────────────────

    @staticmethod
    def _level_flag(analysis_level: str) -> str:
        # Binary --analysis-level expects an integer: 1=symbol_table, 2=call_graph.
        if analysis_level == AnalysisLevel.call_graph:
            return "2"
        return "1"

    @staticmethod
    def _check_existing_analysis(analysis_json_path_file: Path, analysis_level: str) -> bool:
        if not analysis_json_path_file.exists():
            return False
        try:
            with open(analysis_json_path_file) as f:
                data = json.load(f)
            if analysis_level == AnalysisLevel.call_graph and not data.get("call_graph"):
                return False
            if "symbol_table" not in data:
                return False
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def _init_codeanalyzer(self) -> GoApplication:
        exec_cmd = self._get_codeanalyzer_exec()
        level_flag = self._level_flag(self.analysis_level)

        if self.analysis_json_path is None:
            # Pipe mode: write to a temp location the caller manages via analysis_json_path.
            # In practice, callers always pass analysis_json_path; this branch is a safety net.
            import tempfile
            tmp_dir = tempfile.mkdtemp(prefix="cldk-go-")
            return self._run_and_parse(exec_cmd, level_flag, Path(tmp_dir))

        analysis_file = self.analysis_json_path / "analysis.json"
        needs_run = self.eager_analysis or not self._check_existing_analysis(analysis_file, self.analysis_level)

        if needs_run:
            self._run_and_parse(exec_cmd, level_flag, self.analysis_json_path)

        with open(analysis_file) as f:
            return GoApplication(**json.load(f))

    def _run_and_parse(self, exec_cmd: List[str], level_flag: str, output_dir: Path) -> GoApplication:
        output_dir.mkdir(parents=True, exist_ok=True)
        args = exec_cmd + [
            "--input", str(self.project_dir),
            "--output", str(output_dir),
            "--analysis-level", level_flag,
        ]
        if self.cache_dir:
            args += ["--cache", str(self.cache_dir)]

        logger.info("Running codeanalyzer-go: %s", " ".join(args))
        try:
            result: CompletedProcess = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise CodeanalyzerExecutionException(
                f"codeanalyzer-go failed (exit {e.returncode}):\n{e.stderr}"
            ) from e
        except Exception as e:
            raise CodeanalyzerExecutionException(str(e)) from e

        analysis_file = output_dir / "analysis.json"
        if not analysis_file.exists():
            raise CodeanalyzerExecutionException(
                "codeanalyzer-go did not produce analysis.json"
            )
        with open(analysis_file) as f:
            return GoApplication(**json.load(f))

    # ── Public accessors ───────────────────────────────────────────────────────

    def get_application(self) -> GoApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, GoFile]:
        return self.application.symbol_table

    def get_all_files(self) -> Dict[str, GoFile]:
        return self.application.symbol_table

    def get_file(self, file_path: str) -> Optional[GoFile]:
        return self.application.symbol_table.get(file_path)

    def get_all_types(self) -> Dict[str, GoType]:
        """Return all types across all files, keyed by qualified name."""
        result: Dict[str, GoType] = {}
        for file_path, go_file in self.application.symbol_table.items():
            for type_name, go_type in go_file.classes.items():
                result[f"{go_file.module_name}.{type_name}"] = go_type
        return result

    def get_all_callables(self) -> Dict[str, GoCallable]:
        """Return all top-level functions and type methods across all files."""
        result: Dict[str, GoCallable] = {}
        for _, go_file in self.application.symbol_table.items():
            for sig, fn in go_file.functions.items():
                result[sig] = fn
            for _, go_type in go_file.classes.items():
                for sig, method in go_type.methods.items():
                    result[sig] = method
        return result
