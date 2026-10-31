"""Identity boundary checks against available locally built producer artifacts."""

import importlib.util
import os
from pathlib import Path
import pytest
from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, PyCodeAnalyzerConfig
from cldk.analysis.commons.identity import resolve_identity


@pytest.mark.parametrize(
    "language,filename,source",
    [
        ("python", "main.py", "def main():\n    return 1\n"),
        ("typescript", "main.ts", "export function main(): number { return 1; }\n"),
        ("java", "Main.java", "public class Main { public static int main() { return 1; } }\n"),
        ("go", "main.go", "package main\nfunc main() {}\n"),
    ],
)
def test_local_producer_preserves_requested_identity(language, filename, source, tmp_path):
    if language in ["typescript", "go"] and not os.environ.get({"typescript": "CODEANALYZER_TS_BIN", "go": "CODEANALYZER_GO_BIN"}[language]):
        pytest.skip("requires the reviewed local producer binary override")
    if language == "java" and importlib.util.find_spec("codeanalyzer_java") is None:
        pytest.skip("requires the Java analyzer extra")
    project = tmp_path / "project"
    project.mkdir()
    (project / filename).write_text(source)
    if language == "go":
        (project / "go.mod").write_text("module identitytest\n\ngo 1.21\n")
    config_cls = PyCodeAnalyzerConfig if language == "python" else CodeAnalyzerConfig
    config = config_cls(cache_dir=tmp_path / "cache", application_name="-svc %雪", namespace="team%")
    analysis = getattr(CLDK, language)(project_path=project, analysis_level=AnalysisLevel.symbol_table, backend=config)
    application = analysis.get_application_view()
    expected = resolve_identity(config.application_name, config.namespace)
    assert application.id == expected.application_id
    assert application.application_name == config.application_name
    assert application.identity_namespace == config.namespace
    assert analysis.backend.analysis.identity_version == 2
    assert application.symbol_table
    assert all(module.id.startswith(expected.application_id + f"/{language}/") for module in application.symbol_table.values())
