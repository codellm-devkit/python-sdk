"""Canonical module joins must retain owners and reject inconsistent projections."""
from collections import defaultdict

import pytest

from cldk.analysis.commons import identity


ROWS = [
    {"module_id": "can://shop/currency/javascript/server.js", "file_key": "server.js"},
    {"module_id": "can://shop/payment/javascript/server.js", "file_key": "server.js"},
    {"module_id": "can://shop/currency/javascript/server.js", "file_key": "server.js"},
]


def test_index_preserves_canonical_owners_independent_of_order():
    expected = {row["module_id"]: row for row in ROWS}
    assert identity.index_module_rows(ROWS) == expected
    assert identity.index_module_rows(reversed(ROWS)) == expected
    secondary = defaultdict(set)
    for module_id, row in identity.index_module_rows(ROWS).items():
        secondary[row["file_key"]].add(module_id)
    assert secondary == {"server.js": set(expected)}
    assert len([mid for mid in expected if mid == ROWS[0]["module_id"]]) == 1


def test_index_rejects_conflicting_file_keys():
    with pytest.raises(ValueError, match="Conflicting file keys.*can://shop/currency/javascript/server.js"):
        identity.index_module_rows([ROWS[0], {**ROWS[0], "file_key": "other.js"}])


@pytest.mark.parametrize("module_id", [
    "can://shop/payment/javascript/server.js",
    "can://shop/currency/python/server.js",
    "can://shop/currency-other/javascript/server.js",
])
def test_relative_index_rejects_foreign_owner_or_language(module_id):
    index = identity.index_module_rows([{"module_id": module_id, "file_key": "server.js"}])
    with pytest.raises(ValueError, match="outside the bound application/language scope"):
        identity.scoped_module_keys(index, "can://shop/currency", ("javascript", "typescript"))


def test_relative_index_rejects_two_language_candidates_for_one_filename():
    rows = [ROWS[0], {"module_id": "can://shop/currency/typescript/server.js", "file_key": "server.js"}]
    index = identity.index_module_rows(rows)
    assert len(index) == 2
    with pytest.raises(ValueError, match="Ambiguous module file key"):
        identity.scoped_module_keys(index, "can://shop/currency", ("javascript", "typescript"))
