from typing import Any
from cldk import CLDK
from cldk.models.java.models import JCompilationUnit, JImport


def _build_compilation_unit_payload(imports: list[Any], import_declarations: list[Any] | None = None) -> dict[str, Any]:
    payload = {
        "file_path": "/tmp/T.java",
        "package_name": "",
        "comments": [],
        "imports": imports,
        "type_declarations": {},
    }
    if import_declarations is not None:
        payload["import_declarations"] = import_declarations
    return payload


def test_jcompilationunit_supports_legacy_import_list() -> None:
    """Should keep legacy imports and synthesize structured declarations."""
    compilation_unit = JCompilationUnit(**_build_compilation_unit_payload(imports=["java.util.List"]))
    assert compilation_unit.imports == ["java.util.List"]
    assert len(compilation_unit.import_declarations) == 1
    assert isinstance(compilation_unit.import_declarations[0], JImport)
    assert compilation_unit.import_declarations[0].path == "java.util.List"
    assert compilation_unit.import_declarations[0].is_static is False
    assert compilation_unit.import_declarations[0].is_wildcard is False


def test_jcompilationunit_supports_structured_import_list() -> None:
    """Should derive legacy imports from structured import declarations."""
    compilation_unit = JCompilationUnit(
        **_build_compilation_unit_payload(
            imports=[
                {"path": "Foo.bar", "is_static": True, "is_wildcard": False},
                {"path": "Foo.bar", "is_static": False, "is_wildcard": True},
            ]
        )
    )
    assert compilation_unit.imports == ["Foo.bar", "Foo.bar"]
    assert len(compilation_unit.import_declarations) == 2
    assert compilation_unit.import_declarations[0].is_static is True
    assert compilation_unit.import_declarations[0].is_wildcard is False
    assert compilation_unit.import_declarations[1].is_static is False
    assert compilation_unit.import_declarations[1].is_wildcard is True


def test_jcompilationunit_uses_imports_when_import_declarations_is_empty() -> None:
    """Should preserve imports when import_declarations is present but empty."""
    compilation_unit = JCompilationUnit(
        **_build_compilation_unit_payload(
            imports=["java.util.List"],
            import_declarations=[],
        )
    )
    assert compilation_unit.imports == ["java.util.List"]
    assert len(compilation_unit.import_declarations) == 1
    assert compilation_unit.import_declarations[0].path == "java.util.List"


def test_jcompilationunit_prefers_non_empty_import_declarations() -> None:
    """Should prefer structured import_declarations over imports when non-empty."""
    compilation_unit = JCompilationUnit(
        **_build_compilation_unit_payload(
            imports=["legacy.Value"],
            import_declarations=[{"path": "structured.Value", "is_static": True, "is_wildcard": False}],
        )
    )
    assert compilation_unit.imports == ["structured.Value"]
    assert len(compilation_unit.import_declarations) == 1
    assert compilation_unit.import_declarations[0].path == "structured.Value"
    assert compilation_unit.import_declarations[0].is_static is True
    assert compilation_unit.import_declarations[0].is_wildcard is False


def test_jcompilationunit_imports_round_trip_through_dump_apis() -> None:
    """Should preserve import fields across model dump and re-parse flows."""
    original = JCompilationUnit(
        **_build_compilation_unit_payload(
            imports=["legacy.Value"],
            import_declarations=[
                {"path": "structured.Value", "is_static": True, "is_wildcard": False},
                {"path": "structured.Value", "is_static": False, "is_wildcard": True},
            ],
        )
    )

    from_dump = JCompilationUnit(**original.model_dump())
    from_json = JCompilationUnit.model_validate_json(original.model_dump_json())

    expected_imports = ["structured.Value", "structured.Value"]
    expected_declarations = [
        ("structured.Value", True, False),
        ("structured.Value", False, True),
    ]

    for reparsed in [from_dump, from_json]:
        assert reparsed.imports == expected_imports
        assert [(item.path, item.is_static, item.is_wildcard) for item in reparsed.import_declarations] == expected_declarations


def test_get_class_call_graph(analysis_json_fixture):
    """Initialize the CLDK object with the project directory, language, and analysis_backend."""
    cldk = CLDK(language="java")
    analysis = cldk.analysis(project_path=analysis_json_fixture, analysis_json_path=analysis_json_fixture, eager=False, analysis_level="call-graph")
    assert analysis is not None
