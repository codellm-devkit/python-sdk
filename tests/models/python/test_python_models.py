def test_v2_models_are_reexported():
    from cldk.models.python import (
        Span,
        BodyNode,
        CfgEdge,
        CdgEdge,
        DdgEdge,
        SummaryEdge,
        ParamEdge,
        PyDecorator,
        PyEntrypoint,
        PyExternalSymbol,
        PyArtifact,
        PyDependency,
        PyConfigKey,
        PyConfigUseEdge,
        PyAnalyzerInfo,
    )
    from dataclasses import fields

    assert {f.name for f in fields(Span)} >= {"start", "end", "bytes"}
    assert "callee" in {f.name for f in fields(BodyNode)}
    assert {"var", "prov"} <= {f.name for f in fields(DdgEdge)}


def test_v1_names_still_exported():
    """D4: the existing surface does not move."""
    import cldk.models.python as m

    for name in (
        "PyApplication",
        "PyCallEdge",
        "PyCallable",
        "PyCallableParameter",
        "PyCallsite",
        "PyClass",
        "PyClassAttribute",
        "PyComment",
        "PyImport",
        "PyModule",
        "PySymbol",
        "PyVariableDeclaration",
    ):
        assert hasattr(m, name), name
