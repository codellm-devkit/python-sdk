from typing import List, Tuple
from cldk import CLDK


def test_get_class_call_graph(analysis_json_fixture):
    """Initialize the CLDK object with the project directory, language, and analysis_backend."""
    cldk = CLDK(language="java")
    analysis = cldk.analysis(project_path=analysis_json_fixture, analysis_json_path=analysis_json_fixture, eager=False, analysis_level="call-graph")
    assert analysis is not None
