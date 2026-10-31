"""Read-only attachment to two namespaces and prefix-neighbor application anchors."""

import pytest
from cldk.analysis.go.neo4j.neo4j_backend import GoNeo4jBackend, _REQUIRED_RELS
from cldk.utils.exceptions import CodeanalyzerExecutionException


class Record:
    def __init__(self, row):
        self.row = row

    def data(self):
        return self.row


class Driver:
    def __init__(self, marker=2, raw_namespace=None):
        self.statements = []
        self.anchors = {
            f"can://{namespace}/{app}": dict(id=f"can://{namespace}/{app}", identity_version=marker, application_name=app, identity_namespace=raw_namespace or namespace)
            for namespace in ["one", "two"]
            for app in ["svc", "svc-extra"]
        }

    def session(self, database=None):
        return self

    def run(self, query, **params):
        self.statements.append((query, params))
        if "db.relationshipTypes" in query:
            rows = [dict(relationshipType=rel) for rel in _REQUIRED_RELS]
        elif "RETURN count(a)" in query:
            rows = [dict(n=int(params["app_id"] in self.anchors))]
        elif "AS identity" in query:
            rows = [dict(identity=self.anchors[params["app_id"]])] if params["app_id"] in self.anchors else []
        elif "GO_HAS_MODULE" in query:
            # Equal application-relative names must never merge ownership.
            anchor = params["app_id"]
            rows = [dict(p=dict(id=anchor + "/go/server.go", source=anchor, package="main"))]
        else:
            rows = []
        return [Record(row) for row in rows]

    def close(self):
        pass


def test_same_module_names_are_bound_to_namespace_and_application():
    driver = Driver()
    for namespace in ["one", "two"]:
        for app in ["svc", "svc-extra"]:
            backend = GoNeo4jBackend._from_driver(driver, application_name=app, namespace=namespace)
            modules = backend.get_symbol_table()
            assert set(modules) == {"server.go"}
            assert modules["server.go"].source == f"can://{namespace}/{app}"
            assert backend.get_application_view().identity_namespace == namespace
    assert all(not any(word in query.upper().split() for word in ["CREATE", "MERGE", "SET", "DELETE"]) for query, _ in driver.statements)


@pytest.mark.parametrize("marker,namespace", [(None, None), (1, None), (2, "foreign")])
def test_legacy_or_foreign_anchor_metadata_rejected(marker, namespace):
    with pytest.raises(CodeanalyzerExecutionException, match="identity"):
        GoNeo4jBackend._from_driver(Driver(marker, namespace), application_name="svc", namespace="one")


def test_current_producer_defined_type_parses_regenerated_corpus():
    from pathlib import Path
    from cldk.models.go import GoAnalysis

    payload = Path(__file__).resolve().parents[2] / "resources/go/analysis.json"
    analysis = GoAnalysis.model_validate_json(payload.read_text())
    defined = [ty for module in analysis.application.symbol_table.values() for ty in module.types.values() if ty.kind == "defined"]
    assert len(defined) == 8
    assert all(ty.id.startswith("can://litestream/litestream/go/") for ty in defined)


@pytest.mark.parametrize("uri", ["can://two/svc/go/server.go/T", "can://one/svc-extra/go/server.go/T", "can://one/svc/python/server.go/T"])
def test_point_lookup_rejects_foreign_owner_or_language_before_query(uri):
    driver = Driver()
    backend = GoNeo4jBackend._from_driver(driver, application_name="svc", namespace="one")
    before = len(driver.statements)
    with pytest.raises(ValueError, match="scope"):
        backend.get_class(uri)
    assert len(driver.statements) == before
