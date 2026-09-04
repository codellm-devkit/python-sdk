def test_v2_models_are_reexported():
    from cldk.models.python import (
        Span, BodyNode, CfgEdge, CdgEdge, DdgEdge, SummaryEdge, ParamEdge,
        PyDecorator, PyEntrypoint, PyExternalSymbol,
        PyArtifact, PyDependency, PyConfigKey, PyConfigUseEdge, PyAnalyzerInfo,
    )
    assert Span.model_fields.keys() >= {"start", "end", "bytes"}
    assert "callee" in BodyNode.model_fields          # the one sanctioned null slot
    assert {"var", "prov"} <= DdgEdge.model_fields.keys()

def test_v1_names_still_exported():
    """D4: the existing surface does not move."""
    import cldk.models.python as m
    for name in ("PyApplication", "PyCallEdge", "PyCallable", "PyCallableParameter",
                 "PyCallsite", "PyClass", "PyClassAttribute", "PyComment", "PyImport",
                 "PyModule", "PySymbol", "PyVariableDeclaration"):
        assert hasattr(m, name), name
