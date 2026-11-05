################################################################################
# Copyright IBM Corporation 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Go's id and name grammar — the four traps of spec §7 (python-sdk#436), in one place.

Each function here exists because a rule that holds for Python or TypeScript does **not** hold for
Go, and the shared helpers in :mod:`cldk.analysis.commons` would therefore give a wrong answer
rather than an error. They are separated from both backends so the local and the Neo4j backend
cannot drift on them: a body-node id split one way in one backend and another way in the other is
precisely the class of bug a parity contract test cannot see, because both backends would be
*consistently* wrong about the same external callable.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from cldk.analysis.commons.resolve import value_candidate

#: The marker the analyzer puts in a library stand-in's **callable** id, before any vertex ``@``:
#: ``can://<ns>/<app>/@external/bytes/Buffer.Read``. Its leading ``@`` is why
#: :func:`split_body_node_id` may not split on the first one.
EXTERNAL_MARKER = "/@external/"


def body_node_id(callable_id: str, body_key: str) -> str:
    """The analyzer's own global id for one of a callable's body nodes.

    The emitter's rule, adopted rather than re-derived — a synthetic key owns a leading ``@``
    (``"@entry"``, ``"@formal_in:1"``) and a statement key does not (``"10:25"``), so joining with
    an unconditional ``"@"`` would mint ``…Add@@formal_in:0``, which names nothing in ``body`` and
    nothing in the graph. Verified against real ``cango`` 0.3.0 output: every ``body`` entry's own
    ``id`` field equals this function applied to its map key.

    Identical in form to Python's ``body_node_id``; it is restated here rather than imported
    because :func:`split_body_node_id` — its inverse — is *not* shared, and keeping the pair
    together is what makes the asymmetry visible.
    """
    return f"{callable_id}{body_key}" if body_key.startswith("@") else f"{callable_id}@{body_key}"


def split_body_node_id(node_id: str, callable_ids: Iterable[str]) -> Tuple[str, str] | None:
    """``(callable id, body key)`` for ``node_id``, or ``None`` if no known callable owns it.

    **Trap 1 of spec §7, and the reason this function is not a one-line partition.** The obvious
    inverse of :func:`body_node_id` is ``node_id.partition("@")`` — which is what the Python
    backend can afford, because a Python callable id contains no ``@``. A Go library stand-in's id
    does::

        can://dataflow/dataflow/@external/bytes/Buffer.Read@formal_out:0
                                ^ first @ is the external marker, not the vertex separator

    Splitting there yields the callable ``can://dataflow/dataflow/`` and the key
    ``external/bytes/Buffer.Read@formal_out:0``: two strings that name nothing, handed onward as if
    they did. So the owner is found by **stripping a known callable-id prefix** instead, longest
    first — the longest match is the right one because a callable id can be a proper prefix of a
    nested closure's (``…flow.Closure`` and ``…flow.Closure.closure_1``), and the shorter would
    claim the longer's vertices.

    Args:
        node_id: A body node's absolute ``can://`` id.
        callable_ids: Every callable id in the application, stand-ins included. Order is
            irrelevant; this sorts.

    Returns:
        ``(callable_id, body_key)`` where ``body_node_id(*result) == node_id``, or ``None`` when no
        candidate owns it — a miss is returned, not raised, because the callers here are describing
        a walk's nodes and an unknown node is a node to skip, not an error to propagate.
    """
    for cid in sorted(callable_ids, key=len, reverse=True):
        if not node_id.startswith(cid) or node_id == cid:
            continue
        rest = node_id[len(cid) :]
        # The join inserted a bare "@" only for a statement key; a synthetic key brought its own.
        if rest.startswith("@@"):
            return cid, rest[1:]
        if rest.startswith("@"):
            # Either a synthetic key whose "@" is its own, or a statement key whose "@" was the
            # join. Both spellings are tried by the callers against the real ``body`` map, which is
            # the only thing that can tell "@formal_in:0" from a joined "formal_in:0"; what matters
            # here is that the *callable* half is right, and it is.
            return cid, rest
    return None


def go_value_names(var: str) -> List[str]:
    """Every spelling a caller may use for the value whose analyzer ``var`` is ``var``.

    **Trap 3 of spec §7.** :func:`~cldk.analysis.commons.resolve.value_candidate` renders a Go
    global's ``var`` — ``<global>:example.com/dataflow/libflow::Last`` — as the dotted
    ``example.com/dataflow/libflow.Last``. That is the right *display* name, but the separators
    inside its module half are ``/``, and
    :func:`~cldk.analysis.commons.resolve.segment_match` narrows on ``.`` — so the spelling a Go
    programmer actually writes, ``libflow.Last``, matches **nothing**::

        segment_match("libflow.Last", "example.com/dataflow/libflow.Last")  -> False
        segment_match("Last",         "example.com/dataflow/libflow.Last")  -> True   (bare leaf)

    Verified on all 28 global ports of the ``testdata/dataflow`` fixture. Rather than loosen
    ``segment_match`` for every language, Go adds the package-qualified spelling as a second name
    for the same value. A parameter or a capture has exactly one name, so this is the identity
    there and costs nothing.

    The names are what a resolver matches against; the **first** is the canonical one
    :func:`~cldk.analysis.commons.resolve.value_candidate` chose, so an error message and a
    returned :class:`~cldk.analysis.commons.results.SliceNode` still read in one vocabulary.
    """
    v = value_candidate(var)
    if v.kind != "global" or not v.defined_in:
        return [v.name]
    package = v.defined_in.rsplit("/", 1)[-1]
    qualified = f"{package}.{v.leaf}"
    return [v.name] if qualified == v.name else [v.name, qualified]


def receiver_of(body: dict) -> str | None:
    """The ``of`` of this callable's ``formal_in:0``, or ``None``.

    **Trap 2 of spec §7, stated as the thing it protects.** A Go method's ``formal_in:0`` is its
    *receiver*, which never appears in ``GoCallable.parameters`` — so a backend that paired
    ``formal_in:N`` with ``parameters[N]`` would name every one of a method's formals one position
    off, silently. Measured on the ``testdata/dataflow`` fixture: ``formal_in:0.of`` equals
    ``parameters[0].name`` on only 174 of 276 callables, and on a method it reads ``_recv`` or the
    receiver variable's own name while ``parameters`` is empty or starts at the first real argument.

    The rule the backends follow instead: **resolve a formal by its own ``of``, never by ordinal
    into ``parameters``.** This accessor exists for the one case where the receiver specifically is
    wanted; everything else reads ``of`` directly off the vertex.
    """
    node = body.get("@formal_in:0")
    return getattr(node, "of", None) if node is not None else None


def is_external(node_id: str) -> bool:
    """Is this id (or any vertex of it) a library stand-in rather than project code?

    The test is for :data:`EXTERNAL_MARKER`, not for a position within the id: the marker sits in
    the *callable* half, so it answers the same for a stand-in's callable id and for any of its
    vertex ids.
    """
    return EXTERNAL_MARKER in node_id


def callable_match_names(signature: str) -> Tuple[str, ...]:
    """The spellings ``resolve_callable`` accepts for a Go callable, beyond its full signature.

    Spec §7 requires ``DB.Query``, ``(*DB).Query``, ``sql.DB.Query`` and ``database/sql.DB.Query``
    all to name one method. A Go signature is ``<import path>.<Type>.<Method>``
    (``example.com/dataflow/httpsql.sqlRepo.Find``), so:

    * the full signature and any **dot**-separated suffix of it are already handled by
      :func:`~cldk.analysis.commons.resolve.segment_match` — that covers ``Find`` and
      ``sqlRepo.Find``;
    * the **slash** boundary is not, exactly as for a global's module half (:func:`go_value_names`),
      so ``httpsql.sqlRepo.Find`` needs the package-qualified spelling as an explicit second name;
    * Go's pointer-receiver syntax ``(*T).M`` is a spelling the analyzer never emits, so it is
      added as a third.

    Returned as :attr:`~cldk.analysis.commons.resolve.CallableCandidate.match_names`, whose
    contract is that a candidate matched by *any* of its names counts once — so these widen what
    resolves without multiplying ambiguities.
    """
    names = [signature]
    head, _, tail = signature.rpartition("/")
    if head:
        names.append(tail)  # package-qualified: httpsql.sqlRepo.Find
    # (*T).M for the last two dotted segments, where there are two to take.
    parts = names[-1].split(".")
    if len(parts) >= 2:
        names.append(f"(*{parts[-2]}).{parts[-1]}")
    return tuple(dict.fromkeys(names))


def value_display(var: str) -> Tuple[str, str, "str | None"]:
    """``(kind, leaf, defined_in)`` for a ``formal_in`` vertex's ``var``, in the caller's vocabulary.

    A thin pass-through to :func:`~cldk.analysis.commons.resolve.value_candidate`, named here so
    both Go backends describe a port identically and neither reads the raw ``<global>:…`` /
    ``<capture>:…`` markers into a user-facing field. Go's markers are the analyzer's own
    (``internal/sdg/vertices.go``) and are spelled exactly as Python's, which is why the shared
    translation applies unchanged.
    """
    v = value_candidate(var)
    return v.kind, v.leaf, v.defined_in


def dedupe(items: Sequence[str]) -> List[str]:
    """``items`` with duplicates dropped, first occurrence winning — order is part of the contract
    wherever a resolver's candidate list feeds an error message."""
    return list(dict.fromkeys(items))
