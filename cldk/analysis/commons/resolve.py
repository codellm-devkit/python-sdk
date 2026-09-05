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

"""Name resolution policy: which candidate a caller's name names, or why it names none.

**This module performs no I/O.** No Cypher, no driver, no symbol table, no filesystem — names and
candidate lists in, one survivor or an exception out. That is not tidiness: it is what lets the
policy be tested exhaustively with no graph attached, and it is the reason the two Python backends
cannot drift on what "ambiguous" means. Both hand their candidates to the *same* functions here;
neither decides for itself.

The policy, in full (leg 1.5, E8):

* **Exact match wins.** A name equal to a candidate resolves to it even when it is also a suffix of
  longer ones.
* **Otherwise, a dotted suffix match on segment boundaries.** ``"execute"`` matches
  ``db.cursor.execute``; ``"cursor.execute"`` narrows it; ``"ute"`` matches neither. This is
  segment matching on a hierarchical name, not a similarity heuristic.
* **One survivor resolves. More than one raises** :class:`~cldk.utils.exceptions.AmbiguousName`
  carrying every match, and names only the ways out still open to *that* caller — never a keyword
  it already used or one its method does not accept. **None raises**
  :class:`~cldk.utils.exceptions.SelectorNotInGraph`.
* **The candidate names are the caller's, not the analyzer's.** :func:`value_candidate` is where a
  ``formal_in`` vertex stops being ``"<global>:payment::AccessError"`` and becomes a ``"global"``
  named ``AccessError``, defined in ``payment`` and addressable as either ``"AccessError"`` or
  ``"payment.AccessError"``. Both backends translate through it, so neither can label a vertex the
  other would label differently.

There is **no similarity scoring anywhere in this module** — no edit distance, no ``difflib``, no
"did you mean". E8 puts typo-tolerant matching out of scope "not in the resolver, not in the error
path", because a guess presented as a correction is exactly the confident-wrong-answer failure the
addressing layer exists to prevent. Every string in an ``AmbiguousName`` genuinely matched.
"""

from __future__ import annotations

from typing import Callable, List, NamedTuple, Optional, Sequence, TypeVar

from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

T = TypeVar("T")

#: The analyzer's own markers on a ``formal_in`` vertex's ``var``
#: (``codeanalyzer/dataflow/sdg.py``): a captured module global is
#: ``"<global>:<module_name>::<name>"`` and a closure capture is ``"<capture>:<name>"``. They are
#: internal vocabulary and must never reach a caller (E6) — :func:`value_candidate` is the one
#: place that reads them, and both backends route through it so they cannot label the same vertex
#: differently.
GLOBAL_PREFIX = "<global>:"
CAPTURE_PREFIX = "<capture>:"


class CallableCandidate(NamedTuple):
    """One callable a name could resolve to, reduced to the three fields the policy needs.

    Both backends project their callables into this shape before resolving, so the policy sees the
    same tuple whether it came from a Cypher row or an in-memory :class:`PyCallable`.

    Attributes:
        signature: The dotted name matched against (``pkg.mod.Class.method``) — never a ``can://``
            id, which is what E6 keeps out of the caller's hands.
        class_signature: The owning class's signature, or ``None`` for a module-level function or a
            closure. What ``in_class=`` is matched against.
        path: The module's repo-relative path (``addons/foo/models/bar.py``) — the same vocabulary
            as ``locate().module.path`` and the symbol table's keys. What ``in_module=`` is matched
            against.
    """

    signature: str
    class_signature: Optional[str]
    path: str


class ValueCandidate(NamedTuple):
    """One value entering a callable, translated out of the analyzer's vocabulary into the
    caller's.

    A ``formal_in`` vertex is not always a parameter: on a real application 84% of them are
    captured module globals and a further fraction are closure captures, and labelling all three
    ``kind="parameter"`` with the analyzer's raw ``var`` in ``name`` put internal strings like
    ``"<global>:payment::AccessError"`` in a field E6 reserves for the caller's vocabulary.

    Attributes:
        name: What the caller matches against. A parameter or a capture is its bare name; a global
            is ``"<module_name>.<name>"``, so :func:`segment_match` narrows it the same way it
            narrows a dotted callable signature. This is the *only* place value names stop being
            flat: measured on a real application, 14,432 (callable, leaf name) pairs carry more
            than one global, and the dotted form is what makes those an ambiguity a caller can
            actually resolve rather than a dead end.
        kind: ``"parameter"``, ``"global"`` or ``"capture"`` — what
            :attr:`~cldk.analysis.commons.results.SliceNode.kind` reports.
        leaf: The readable identifier as it is written in the source (``AccessError``) — what
            :attr:`~cldk.analysis.commons.results.SliceNode.name` reports.
        defined_in: The module the global is defined in, or ``None`` for a parameter or a capture.
    """

    name: str
    kind: str
    leaf: str
    defined_in: Optional[str]


def value_candidate(var: str) -> ValueCandidate:
    """Translate one ``formal_in`` vertex's ``var`` into the caller's vocabulary.

    Adopts the analyzer's own grammar rather than re-deriving it: ``GLOBAL_PREFIX + module + "::"
    + name`` and ``CAPTURE_PREFIX + name`` are minted by ``codeanalyzer/dataflow/sdg.py``, whose
    module qualifier is the module's ``module_name`` — the same string
    :attr:`~cldk.analysis.commons.results.ModuleRef.module_name` already hands callers. **This must
    track that grammar**; a marker the analyzer adds and this does not would surface verbatim.
    """
    if var.startswith(GLOBAL_PREFIX):
        module, sep, name = var[len(GLOBAL_PREFIX) :].partition("::")
        if sep:
            return ValueCandidate(f"{module}.{name}", "global", name, module)
        return ValueCandidate(module, "global", module, None)
    if var.startswith(CAPTURE_PREFIX):
        name = var[len(CAPTURE_PREFIX) :]
        return ValueCandidate(name, "capture", name, None)
    return ValueCandidate(var, "parameter", var, None)


def segment_match(query: str, candidate: str, sep: str = ".") -> bool:
    """Does ``query`` name ``candidate``, as a whole name or as a suffix of it on segment
    boundaries?

    ``sep`` is ``"."`` for dotted names (callable signatures, class signatures) and ``"/"`` for
    module paths, which are the two hierarchical vocabularies the facade speaks. The rule is the
    same in both: equal, or preceded by a separator — so ``"cursor.execute"`` matches
    ``db.cursor.execute`` and ``"ursor.execute"`` matches nothing.
    """
    return candidate == query or candidate.endswith(sep + query)


def _narrow(query: str, candidates: Sequence[str]) -> List[str]:
    """Exact matches if there are any, otherwise every segment-suffix match.

    The two-step is what makes an exact name unambiguous even when longer candidates end with it:
    ``"a.write"`` against ``["a.write", "pkg.a.write"]`` is the first, not a choice between them.
    """
    return [c for c in candidates if c == query] or [c for c in candidates if segment_match(query, c)]


def resolve_name(query: str, candidates: Sequence[str], *, kind: str, narrow_with: str) -> str:
    """The one candidate ``query`` names, or raise saying which way it failed.

    Args:
        query: The name as the caller wrote it.
        candidates: The dotted names to match against — the *whole* domain, deduplicated by the
            caller if it can contain repeats.
        kind: What is being resolved (``"callable"`` / ``"value"``), for the exception messages.
        narrow_with: The keyword(s) that would disambiguate, named in an ``AmbiguousName``.

    Raises:
        AmbiguousName: More than one candidate matched. Carries all of them.
        SelectorNotInGraph: Nothing matched. Names only what the caller wrote — no suggestions.
    """
    hits = _narrow(query, candidates)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SelectorNotInGraph(kind, [query], 1)
    raise AmbiguousName(query, hits, kind=kind, narrow_with=narrow_with)


def resolve_callable_signature(
    name: str,
    candidates: Sequence[CallableCandidate],
    *,
    in_class: Optional[str] = None,
    in_module: Optional[str] = None,
) -> str:
    """The signature of the one callable ``name`` names, narrowed by ``in_class`` / ``in_module``.

    A callable is the unit of address, so these two keywords *disambiguate* rather than scope
    (spec § 5.2): they are applied first, with the same :func:`segment_match` used on the name
    itself — dotted for a class signature, ``"/"``-separated for a module path — so
    ``in_class="PaymentPortal"`` and ``in_class="addons.account_payment.controllers.payment.PaymentPortal"``
    both work, and so do ``in_module="payment.py"`` and the full repo-relative path.

    Args:
        name: The callable name, whole or a dotted suffix of the signature.
        candidates: Every callable in the domain (see the backends' docstrings for what that
            domain is — it must be the same one on both).
        in_class: Keep only callables whose owning class this names. A callable with no owning
            class is excluded outright, not silently kept.
        in_module: Keep only callables whose module path this names.

    Raises:
        AmbiguousName: More than one callable matched.
        SelectorNotInGraph: None did — including the case where ``in_class``/``in_module`` filtered
            away the only match, which is a name that resolves to nothing *as asked*.
    """
    if in_class is not None:
        candidates = [c for c in candidates if c.class_signature and segment_match(in_class, c.class_signature)]
    if in_module is not None:
        candidates = [c for c in candidates if segment_match(in_module, c.path, sep="/")]
    # Only offer the keywords the caller has *not* already used: telling someone who wrote
    # ``in_class="ResPartner"`` to "narrow it with in_class=" is advice they have already taken.
    unused = [kw for kw, given in (("in_class=", in_class), ("in_module=", in_module)) if given is None]
    narrow_with = " or ".join(unused + ["by naming more of the dotted path"]) if unused else "by naming more of the dotted path"
    return resolve_name(name, [c.signature for c in candidates], kind="callable", narrow_with=narrow_with)


def resolve_within(resolve_callable: Callable[[str], "T"], within: str) -> "T":
    """Resolve a ``within=`` argument, re-raising an ambiguity in terms the caller can act on.

    ``resolve_value(name, *, within)`` takes no ``in_class=`` / ``in_module=``, so the advice
    :func:`resolve_callable_signature` gives — the keywords *its own* caller could pass — names
    two keywords this one does not accept. The way out that does exist is naming more of the dotted
    path in ``within=`` itself, and that is what the re-raise says.

    Passthrough keywords were the alternative; they were rejected because they would add two
    parameters that buy nothing ``within="AccountMove.write"`` does not already buy — ``within`` is
    matched segment-wise against the full signature, so it narrows by class and by module already.
    """
    try:
        return resolve_callable(within)
    except AmbiguousName as e:
        raise AmbiguousName(within, e.candidates, kind=e.kind, narrow_with="a longer within= (name more of the dotted path)") from None


def resolve_value_name(name: str, values: Sequence[str], *, within: str) -> str:
    """The one value of ``within`` that ``name`` names.

    ``values`` are :attr:`ValueCandidate.name`s: a parameter or a capture is flat, but a captured
    global is ``"<module_name>.<name>"``, so :func:`segment_match` is *live* here rather than
    degenerating to equality the way it did when every value was assumed to be a parameter.
    ``"AccessError"`` names ``payment.AccessError``; ``"payment.AccessError"`` narrows when several
    modules define one. ``within`` is already-resolved and appears only in the messages, so a
    caller reading the error sees the callable it actually searched, not the abbreviation it typed.

    Raises:
        AmbiguousName: More than one value matched — a bare leaf name where the callable captures
            that global from several modules (measured on a real application: 14,432 such pairs,
            and **no** two values whose full names collide, so writing the qualified name always
            resolves). The message says so; the way out is in the candidates it carries.
        SelectorNotInGraph: No value of ``within`` carries that name.
    """
    return resolve_name(name, values, kind="value", narrow_with=f"the fuller name of one of the candidates, or a different within= (currently {within!r})")
