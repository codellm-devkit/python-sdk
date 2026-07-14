from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Literal
import networkx as nx

Confidence = Literal["resolved", "structural", "unresolved"]


@dataclass(frozen=True)
class FlowPath:
    source: str
    sink: str
    hops: List[Dict] = field(default_factory=list)
    confidence: Confidence = "unresolved"


@dataclass
class GraphResult:
    subgraph: nx.DiGraph
    evidence: List[Dict]
    _explain: Dict

    def uris(self) -> List[str]:
        return [e["uri"] for e in self.evidence]

    def explain(self) -> Dict:
        return dict(self._explain)

    def to_json(self) -> str:
        return json.dumps({"evidence": self.evidence, "explain": self._explain,
                           "vertices": list(self.subgraph.nodes)}, sort_keys=True)

    def __len__(self) -> int:
        return self.subgraph.number_of_nodes()

    def __bool__(self) -> bool:
        return self.subgraph.number_of_nodes() > 0


@dataclass
class SliceResult(GraphResult):
    pass


@dataclass
class FlowResult(GraphResult):
    paths: List[FlowPath] = field(default_factory=list)

    def to_json(self) -> str:
        base = json.loads(super().to_json())
        base["paths"] = [asdict(p) for p in self.paths]
        return json.dumps(base, sort_keys=True)
