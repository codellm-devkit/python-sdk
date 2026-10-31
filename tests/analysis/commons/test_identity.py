from pathlib import Path
import pytest
from cldk.analysis.commons import identity
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, PyCodeAnalyzerConfig, TSCodeAnalyzerConfig
from cldk.analysis.commons.keys import resolve_module_key


def test_identity_defaults_and_raw_encoding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = identity.resolve_identity(None, None, project_root=Path("."))
    assert resolved.application_id == f"can://{tmp_path.name}/{tmp_path.name}"
    value = identity.resolve_identity("-svc %雪", "team%")
    assert value.application_id == "can://team%25/-svc%20%25%E9%9B%AA"
    assert value.application_name == "-svc %雪"


@pytest.mark.parametrize("name", ["", " ", ".", "..", "a/b", "a\\b", "x\n", "\ud800"])
def test_invalid_identity(name):
    with pytest.raises(ValueError):
        identity.resolve_identity(name, None)
    with pytest.raises(ValueError):
        identity.resolve_identity("app", name)


def test_missing_graph_application():
    with pytest.raises(Exception, match="application_name"):
        identity.resolve_identity(None, None)


def test_config_identity_keyword_only():
    assert PyCodeAnalyzerConfig("cache", True, namespace="team", application_name="app").use_ray
    assert TSCodeAnalyzerConfig("cache", True, namespace="team").tsc_only
    assert CodeAnalyzerConfig(namespace="team").application_name is None


def test_scoped_module_resolution(tmp_path):
    keys = ["src/server.js", "server.js"]
    ids = {"server.js": "can://team/svc/javascript/server.js", "src/server.js": "can://team/svc/typescript/src/server.js"}
    kwargs = dict(application_id="can://team/svc", module_ids=ids, source_root=tmp_path)
    assert resolve_module_key(ids["server.js"], keys, **kwargs) == "server.js"
    assert resolve_module_key(str(tmp_path / "src/server.js"), keys, **kwargs) == "src/server.js"
    assert resolve_module_key("./src/../server.js", keys, **kwargs) == "server.js"
    assert resolve_module_key("other/server.js", keys, **kwargs) == "other/server.js"
    for uri in ["can://other/svc/javascript/server.js", "can://team/svc-extra/javascript/server.js", "can://team/svc/python/server.js"]:
        with pytest.raises(ValueError):
            resolve_module_key(uri, keys, **kwargs)
    with pytest.raises(ValueError):
        resolve_module_key("/unbound/server.js", keys)


@pytest.mark.parametrize("language,cls", [("typescript", "TSCodeanalyzer"), ("java", "JCodeanalyzer"), ("go", "GoCodeanalyzer")])
def test_argv_preserves_raw_identity(language, cls, tmp_path):
    import importlib

    analyzer_cls = getattr(importlib.import_module(f"cldk.analysis.{language}.codeanalyzer.codeanalyzer"), cls)
    analyzer = analyzer_cls.__new__(analyzer_cls)
    analyzer.project_dir = tmp_path
    analyzer.identity = identity.resolve_identity("-svc %雪", "online-boutique")
    analyzer.target_files = None
    analyzer.eager_analysis = False
    analyzer._get_codeanalyzer_exec = lambda: ["analyzer"]
    argv = analyzer._argv(1, None)
    assert "--namespace=online-boutique" in argv
    assert "--app-name=-svc %雪" in argv


def test_metadata_required_and_scope_checked():
    bound = identity.resolve_identity("svc", "team")
    raw = dict(identity_version=2, application=dict(identity_version=2, id=bound.application_id, application_name="svc", identity_namespace="team"))
    assert identity.validate_identity(raw, bound) == raw
    for bad in [{**raw, "identity_version": None}, {**raw, "application": {**raw["application"], "identity_namespace": "other"}}]:
        with pytest.raises(Exception, match="identity"):
            identity.validate_identity(bad, bound)


@pytest.mark.parametrize(
    "language,cls,rootprop",
    [
        ("python", "PyNeo4jBackend", "_application_id"),
        ("typescript", "TSNeo4jBackend", "_app_id"),
        ("java", "JNeo4jBackend", "_application_id"),
        ("go", "GoNeo4jBackend", "_app_id"),
    ],
)
def test_graph_scope_identity(language, cls, rootprop, monkeypatch):
    import importlib

    backend_cls = getattr(importlib.import_module(f"cldk.analysis.{language}.neo4j.neo4j_backend"), cls)
    monkeypatch.setattr(backend_cls, "_probe_schema", lambda self: None)
    monkeypatch.setattr(backend_cls, "_probe_identity", lambda self: None, raising=False)
    for method in ["_load_module_keys", "_load_modules"]:
        if hasattr(backend_cls, method):
            monkeypatch.setattr(backend_cls, method, lambda self: {})
    if hasattr(backend_cls, "_probe_resolution_edges"):
        monkeypatch.setattr(backend_cls, "_probe_resolution_edges", lambda self: False)
    backend = backend_cls._from_driver(object(), application_name="svc", namespace="team")
    assert getattr(backend, rootprop) == "can://team/svc"
    assert backend._scope_prefix == "can://team/svc/"


def test_go_module_key_retains_application_relative_path():
    from cldk.analysis.go.neo4j.neo4j_backend import GoNeo4jBackend

    backend = GoNeo4jBackend.__new__(GoNeo4jBackend)
    backend.identity = identity.resolve_identity("svc", "team")
    assert backend._module_file_key("can://team/svc/go/src/main.go") == "src/main.go"
    with pytest.raises(ValueError):
        backend._module_file_key("can://other/svc/go/src/main.go")


def test_cache_identity_isolation_and_legacy_rejection(tmp_path):
    import json
    from cldk.analysis.commons.backend_config import cache_subdir

    first = identity.resolve_identity("svc", "one")
    second = identity.resolve_identity("svc", "two")
    assert cache_subdir(tmp_path, None, "go", identity=first) != cache_subdir(tmp_path, None, "go", identity=second)
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(dict(application=dict(identity_version=2, id=first.application_id, application_name="svc", identity_namespace="one"))))
    assert not identity.cache_matches(path, first)
    path.write_text(json.dumps(dict(identity_version=2, application=dict(identity_version=2, id=first.application_id, application_name="svc", identity_namespace="one"))))
    assert identity.cache_matches(path, first)
    assert not identity.cache_matches(path, second)


def test_python_application_marker_is_not_synthesized():
    from codeanalyzer.schema import Analysis
    from cldk.models.python import PyApplication

    bound = identity.resolve_identity("svc", "team")
    application = PyApplication(id=bound.application_id, application_name="svc", identity_namespace="team", symbol_table={})
    analysis = Analysis(identity_version=2, application=application)
    with pytest.raises(Exception, match="identity"):
        identity.validate_identity(analysis, bound)


@pytest.mark.parametrize(
    "language,facade_name,backend_name,config_cls",
    [
        ("python", "PythonAnalysis", "PyCodeanalyzer", PyCodeAnalyzerConfig),
        ("typescript", "TypeScriptAnalysis", "TSCodeanalyzer", CodeAnalyzerConfig),
        ("java", "JavaAnalysis", "JCodeanalyzer", CodeAnalyzerConfig),
        ("go", "GoAnalysis", "GoCodeanalyzer", CodeAnalyzerConfig),
    ],
)
def test_reused_default_config_is_not_bound_to_first_project(language, facade_name, backend_name, config_cls, tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    module = importlib.import_module(f"cldk.analysis.{language}.{language}_analysis")
    calls = []

    class Backend:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def get_application_view(self):
            return SimpleNamespace()

    monkeypatch.setattr(module, backend_name, Backend)
    cfg = config_cls()
    for name in ["first", "second"]:
        root = tmp_path / name
        root.mkdir()
        getattr(module, facade_name)(project_dir=root, analysis_level="symbol_table", target_files=None, eager_analysis=False, backend=cfg)
    assert [call["application_name"] for call in calls] == ["first", "second"]
    assert [call["namespace"] for call in calls] == ["first", "second"]
    assert cfg.application_name is cfg.namespace is None


def test_raw_application_marker_is_required_except_java():
    bound = identity.resolve_identity("svc", "team")
    app = dict(id=bound.application_id, application_name="svc", identity_namespace="team")
    with pytest.raises(Exception, match="identity"):
        identity.validate_identity(dict(identity_version=2, language="python", application=app), bound)
    identity.validate_identity(dict(identity_version=2, language="java", application=app), bound)


def test_absolute_module_path_requires_an_existing_bound_root(tmp_path):
    with pytest.raises(ValueError, match="source root"):
        resolve_module_key(str(tmp_path / "missing/server.js"), ["server.js"], source_root=tmp_path / "missing")


def test_python_graph_module_uri_uses_returned_module_identity():
    from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

    backend = PyNeo4jBackend.__new__(PyNeo4jBackend)
    backend.identity = identity.resolve_identity("app", None)
    backend.application_name = "app"
    canonical = "can://app/app/python/hidden.py"
    backend._run = lambda *args, **kwargs: [dict(k=".hidden.py", id=canonical)]
    backend._modules = backend._load_module_keys()
    assert backend._resolve_paths([canonical]) == [".hidden.py"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"identity_version": 2, "application": []},
        {"identity_version": 2.0, "application": {"identity_version": 2, "id": "can://app/app", "application_name": "app", "identity_namespace": "app"}},
    ],
)
def test_malformed_cached_identity_regenerates_instead_of_being_trusted(payload, tmp_path):
    import json

    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(payload))
    assert not identity.cache_matches(path, identity.resolve_identity("app", None))


def test_changed_namespace_regenerates_a_shared_local_cache(tmp_path, monkeypatch):
    import json
    from cldk.analysis.typescript.codeanalyzer.codeanalyzer import TSCodeanalyzer

    output = tmp_path / "output"
    output.mkdir()
    calls = []

    def producer(argv, **kwargs):
        namespace = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--namespace="))
        calls.append(namespace)
        bound = identity.resolve_identity("svc", namespace)
        raw = dict(
            identity_version=2,
            schema_version="2.0.0",
            language="typescript",
            max_level=1,
            analyzer=dict(name="typescript", version="1.6.4"),
            application=dict(identity_version=2, id=bound.application_id, application_name="svc", identity_namespace=namespace, symbol_table={}),
        )
        (output / "analysis.json").write_text(json.dumps(raw))

    monkeypatch.setattr(TSCodeanalyzer, "_get_codeanalyzer_exec", lambda self: ["producer"])
    monkeypatch.setattr("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run", producer)
    for namespace in ["one", "two", "two"]:
        analyzer = TSCodeanalyzer(tmp_path, output, "symbol_table", False, None, application_name="svc", namespace=namespace)
        assert analyzer.application.identity_namespace == namespace
    assert calls == ["one", "two"]
