"""Canonical IDs survive graph-to-model reconstruction for namespace-scoped joins."""

import pytest

from cldk.analysis.python.neo4j import reconstruct


@pytest.mark.parametrize(
    ('kind', 'expected'),
    [
        ('module', 'can://online-boutique/recommendationservice/python/server.py'),
        ('class', 'can://online-boutique/recommendationservice/python/server.py/Recommendation'),
        ('callable', 'can://online-boutique/recommendationservice/python/server.py/Recommendation/run'),
    ],
)
def test_graph_declarations_preserve_the_returned_canonical_id(kind, expected):
    props = {'id': expected, 'name': 'Recommendation', 'signature': 'server.Recommendation'}
    if kind == 'module':
        value = reconstruct.module(props, file_key='server.py')
    elif kind == 'class':
        value = reconstruct.class_(props)
    else:
        value = reconstruct.callable_(props)
    assert value.id == expected
