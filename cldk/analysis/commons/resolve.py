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
  it already used, never one its method does not accept, and never one that cannot split the very
  matches it is listing. **None raises**
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

from typing import Callable, Collection, Dict, List, NamedTuple, Optional, Sequence, Tuple, TypeVar, Union

from cldk.analysis.commons.keys import module_dotted
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

#: The analyzer's marker for a callable's *result*, carried as the ``var`` of a ``formal_out`` or
#: ``actual_out`` vertex (33,223 of them on a real application, against 546,374 ``<global>:`` and
#: 1,577 ``<capture>:``). Internal vocabulary like the other two, and unlike them it does not
#: translate into a name — a returned value has none; ``kind="return"`` is what identifies it.
RETURN_MARKER = "<return>"


class CallableCandidate(NamedTuple):
    """One callable a name could resolve to, reduced to the fields the policy needs.

    Both backends project their callables into this shape before resolving, so the policy sees the
    same tuple whether it came from a Cypher row or an in-memory :class:`PyCallable`.

    Attributes:
        signature: The dotted name **returned and reported** (``pkg.mod.Class.method``) — never a
            ``can://`` id, which is what E6 keeps out of the caller's hands. Also what a name is
            matched against, unless ``match_names`` says otherwise.
        class_signature: The owning class's signature, or ``None`` for a module-level function or a
            closure. What ``in_class=`` is matched against.
        path: The module's repo-relative path (``addons/foo/models/bar.py``) — the same vocabulary
            as ``locate().module.path`` and the symbol table's keys. What ``in_module=`` is matched
            against.
        match_names: The spellings this callable *answers to*, when they are not just
            :attr:`signature`. Empty means ``(signature,)``, which is every Python and TypeScript
            callable. Java needs more than one: its signature carries a parameter tail
            (``…TradeDirect.cancelOrder(java.lang.Integer, boolean)``) that a caller writing
            ``"cancelOrder"`` has not typed, so the name is matched against the signature *and*
            against the same signature with the tail cut, while the tail-carrying form stays what
            resolves an overload exactly and what an ambiguity lists (J-3). Empty is the right
            spelling of "none" here: a callable always answers to *something*, so "no match names"
            can only mean "the signature is the name" — there is no second reading to confuse it
            with, which is why this field defaults to ``()`` and :attr:`module_names` does not.
        module_names: The dotted spellings of this callable's module. ``None`` — the default —
            means "this language does not supply them; derive one with ``module_dotted(path)``",
            which is what Python and TypeScript want. Java supplies its own: a Java module's
            dotted name is its **declared package**, and deriving it from the path yields
            ``src.main.java.com.ibm…``, which names nothing (J-2). **An empty tuple is not the
            default**: it means the language supplied names and there are none — a unit in the
            default package declaring no type — and such a candidate answers to no dotted module
            spelling at all rather than falling back to a derivation from a path that is a build
            layout. Conflating the two is how a ``.java`` path would silently be matched against
            a ``.py``-suffixed derivation, which is exactly what J-2 forbids.
    """

    signature: str
    class_signature: Optional[str]
    path: str
    match_names: Tuple[str, ...] = ()
    module_names: Optional[Tuple[str, ...]] = None


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


def _narrow_callables(query: str, candidates: Sequence[CallableCandidate]) -> List[CallableCandidate]:
    """:func:`_narrow`, over candidates that may answer to more than one spelling.

    The two-step is unchanged — exact matches if there are any, otherwise every segment-suffix
    match — it just runs over :attr:`CallableCandidate.match_names` instead of the bare signature,
    and a candidate matched by either of its names counts once. With the default empty
    ``match_names`` this *is* :func:`_narrow` on signatures.
    """
    names = [(c, c.match_names or (c.signature,)) for c in candidates]
    exact = [c for c, ns in names if query in ns]
    return exact or [c for c, ns in names if any(segment_match(query, n) for n in ns)]


def _module_names(c: CallableCandidate, dotted: Callable[[str], str] = module_dotted) -> Tuple[str, ...]:
    """The dotted spellings ``in_module=`` matches this candidate's module against.

    The test is ``is None``, not truthiness, and that is the whole point of the function: an empty
    :attr:`CallableCandidate.module_names` means *the language supplied none* — a Java unit in the
    default package declaring no type — and must stay distinct from ``None``, which means *derive
    it from the path*. Falling back on a falsy empty would hand a ``.java`` path to a derivation
    whose default suffix list is ``(".py",)``, so nothing is stripped and the ``.java`` rides into
    the dotted name; the result would then *match* some caller spellings, presenting a path
    derivation J-2 forbids as a successful resolution.
    """
    return (dotted(c.path),) if c.module_names is None else c.module_names


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
    dotted: Callable[[str], str] = module_dotted,
    by_full_name: str = "more of the dotted path",
) -> str:
    """The signature of the one callable ``name`` names, narrowed by ``in_class`` / ``in_module``.

    A callable is the unit of address, so these two keywords *disambiguate* rather than scope
    (spec § 5.2): they are applied first, with the same :func:`segment_match` used on the name
    itself — dotted for a class signature, ``"/"``-separated for a module path — so
    ``in_class="PaymentPortal"`` and ``in_class="addons.account_payment.controllers.payment.PaymentPortal"``
    both work, and so do ``in_module="payment.py"`` and the full repo-relative path.

    **``in_module`` also takes the dotted module name** (``"odoo.tools.mail"``, or a dotted suffix
    of it such as ``"tools.mail"`` / ``"mail"``), derived from the path by :func:`module_dotted`.
    That is the vocabulary a caller *reads*: a signature is ``odoo.tools.mail.email_domain_extract``,
    and ``in_class=`` is a dotted suffix already, so a module named the way it appears in every
    signature must work too. (The analyzer's ``module_name`` — what ``ModuleRef.module_name`` and
    ``SliceNode.defined_in`` carry — is the *bare* last segment, ``"mail"``, which the dotted rule
    also accepts.) A path spelling with ``/`` never matches dotted and vice versa, so the widening
    adds no ambiguity: a dotted ``"mail"`` names only a module whose dotted name *ends* in
    ``.mail``, not everything under a ``mail/`` package. A candidate that carries
    :attr:`CallableCandidate.module_names` is matched against those instead: the derivation is a
    *convention*, and Java's is different enough that deriving it silently names nothing (J-2).

    **An ambiguity's advice names only keywords that could work.** ``in_class=`` is offered only
    when the matches disagree on their owning class and ``in_module=`` only when they disagree on
    their module: two overloads of one Java method, or two ``__init__``s of one Python class, are
    an ambiguity neither keyword can split, and naming one anyway is advice a caller can follow to
    the same exception (E8). What is always offered is ``by_full_name``, because the way out that
    needs no keyword is always open.

    **The error names the argument that actually missed.** ``callers_of("x", in_module="…")``
    used to fail with ``callable not in graph: 'x'`` when ``x`` plainly existed and it was the
    module that matched nothing. When the name matches but a keyword filters every match away,
    the raise is about that keyword.

    Args:
        name: The callable name, whole or a dotted suffix of the signature.
        candidates: Every callable in the domain (see the backends' docstrings for what that
            domain is — it must be the same one on both).
        in_class: Keep only callables whose owning class this names. A callable with no owning
            class is excluded outright, not silently kept.
        in_module: Keep only callables whose module this names, by path or by dotted name.
        dotted: How this language spells a module path as a dotted name -- the Python default
            strips a trailing ``/__init__`` and knows only ``.py``. TypeScript passes a
            :func:`~cldk.analysis.commons.keys.module_dotted` bound to its six source extensions
            and ``package_index=None``, because ``__init__.ts`` is a module in its own right there.
            One injected function rather than two forwarded keywords: the caller already has to
            know its own convention, and threading each knob separately is how the two backends of
            one language start disagreeing about it.
            A candidate that carries its own ``module_names`` overrides this; the two compose,
            with the explicit names winning and this function used only to derive.
        by_full_name: The last clause of an ambiguity's "narrow it with …" advice — the way out
            that needs no keyword. **A noun phrase**, because the sentence around it reads
            ``Narrow it with {narrow_with}.`` and the clauses either side of it are ``in_class=`` /
            ``in_module=``; a verb phrase there produced "Narrow it with by naming …", which is now
            the reading of *every* Java overload ambiguity since the keyword pruning leaves this as
            the only clause. It is a parameter because it is language-specific and must be
            *true*: more of the dotted path is what splits two Python callables, but it
            cannot split two Java overloads, which differ only in the parameter tail (J-3). An
            instruction that cannot work is the same confident-wrong-answer failure E8 keeps out
            of the error path — and so is one that *describes* a spelling the analyzer does not
            produce, which is why Java's clause points at the listed matches instead.

    Raises:
        AmbiguousName: More than one callable matched.
        SelectorNotInGraph: None did. ``kind`` is ``"callable"`` when the name itself matched
            nothing; ``"in_class"`` / ``"in_module"`` when the name matched and that keyword
            excluded every match — a name that resolves to nothing *as asked*, blamed on the
            argument that asked it.
    """
    filters = {
        "in_class": (in_class, lambda c: bool(c.class_signature) and segment_match(in_class, c.class_signature)),
        "in_module": (
            in_module,
            lambda c: segment_match(in_module, c.path, sep="/")
            or any(segment_match(in_module, d) for d in _module_names(c, dotted)),
        ),
    }
    matched = _narrow_callables(name, candidates)
    for keyword, (given, keep) in filters.items():
        if given is None:
            continue
        candidates = [c for c in candidates if keep(c)]
        if matched and not any(keep(c) for c in matched):
            raise SelectorNotInGraph(keyword, [given], 1, detail=f"{name!r} matches {len(matched)} callable(s), none of them satisfying {keyword}={given!r}")
    hits = _narrow_callables(name, candidates)
    if len(hits) == 1:
        return hits[0].signature
    if not hits:
        raise SelectorNotInGraph("callable", [name], 1)
    # Only offer the keywords the caller has *not* already used **and that could split these very
    # hits**: telling someone who wrote ``in_class="ResPartner"`` to "narrow it with in_class=" is
    # advice they have already taken, and offering it for two overloads of one class — or for two
    # ``__init__``s of one class — is advice that provably cannot work, which is the same
    # confident-wrong-answer failure E8 keeps out of the error path. A keyword splits the hits only
    # if they disagree on what it matches against.
    splits = {"in_class=": {c.class_signature for c in hits}, "in_module=": {c.path for c in hits}}
    unused = [kw for kw, given in (("in_class=", in_class), ("in_module=", in_module)) if given is None and len(splits[kw]) > 1]
    raise AmbiguousName(name, [c.signature for c in hits], kind="callable", narrow_with=" or ".join(unused + [by_full_name]))


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


#: The body-node kinds whose ``var`` names a value being *passed*, and what a caller calls them.
#: ``formal_in`` is absent because it is not a flat rename: a value entering a callable is a
#: parameter, a captured global or a closure capture, and only :func:`value_candidate` can tell
#: which. The other three are unambiguous — an ``actual_in`` is the argument at a call site, and
#: both ``*_out`` vertices are the value coming back out of the call.
_PASSING_KINDS = {"actual_in": "argument", "actual_out": "return", "formal_out": "return"}


def body_node_kind(kind: str, var: Optional[str]) -> tuple[str, Optional[str], Optional[str]]:
    """One body node's ``(kind, name, defined_in)`` in the caller's vocabulary.

    The schema's kinds are the analyzer's: ``formal_in`` / ``actual_in`` / ``formal_out`` /
    ``actual_out`` for parameter passing, and ``statement`` / ``call`` / ``return`` / ``branch`` /
    ``loop`` / ``raise`` / ``handler`` / ``entry`` / ``exit`` for everything else. The second group
    is already English and passes through unchanged; the first is internal spelling (E6) and is
    translated — including the ``"<global>:payment::AccessError"`` markers, through the *same*
    :func:`value_candidate` the resolver uses, so a vertex a caller addressed as a ``global`` does
    not come back from a slice labelled something else.

    A node with nothing to name — a statement, a branch, the synthetic ``entry``/``exit``
    bookends — gets ``name=None`` rather than an invented one.
    """
    if kind == "formal_in" and var:
        v = value_candidate(var)
        return v.kind, v.leaf, v.defined_in
    if kind in _PASSING_KINDS:
        if not var or var == RETURN_MARKER:
            return _PASSING_KINDS[kind], None, None
        return _PASSING_KINDS[kind], value_candidate(var).leaf, None
    return kind, None, None


def resolve_sanitizers(
    sanitizers: Sequence[Union[str, Tuple[str, str]]],
    *,
    resolve_callable: Callable[[str], "T"],
    edge_vars_in: Callable[[str], Collection[str]],
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Turn caller-written sanitizer selectors into what :func:`~cldk.analysis.commons.graphs.sdg_taint_query`
    needs: ``(cuts, cut_callable_ids)``, bound respectively to its ``$cuts`` and ``$cut_callables``.

    Two shapes, two meanings (spec T6) -- a bare string cuts a **callable** (every body node under
    it is off-limits), a ``(name, within)`` pair cuts a **variable**, scoped to the callable
    ``within`` names. They are different mechanisms for a reason: a validating guard
    (``if not re.match(...): abort``) never sits on the data path, so only a variable cut severs
    it; a transforming sanitizer (``html.escape(x)``) does sit on the path and is naturally named
    as the function it is.

    **The shape decides which resolver runs, and neither is a fallback for the other.** A bare
    name that :func:`resolve_callable` cannot resolve raises -- it is never retried as a variable
    selector, because that would need a ``within=`` this shape does not carry, and inventing one
    (or guessing the caller meant something else) is exactly the confident-wrong-answer failure
    the addressing layer exists to prevent. Symmetrically, a pair whose name *does* resolve as a
    callable is not "upgraded" into a bare-string cut; the caller wrote a pair, so it is resolved
    as one, and if that fails it fails loudly.

    A variable selector is **not** validated with ``resolve_value``. Measured on a live graph:
    ``resolve_value`` addresses only ``formal_in`` port vertices -- parameters -- while a cut
    matches ``r.var`` on *edges*, most of which are locals (``cleaned``, ``result``, ``answer``
    and the like never appear as a ``formal_in``). Routing a variable selector through
    ``resolve_value`` would raise :class:`~cldk.utils.exceptions.SelectorNotInGraph` for a
    legitimate sanitizer that appears on real edges, telling the caller it does not exist when it
    does. So the existence check is against ``edge_vars_in(prefix)`` -- the variable names actually
    carried by SDG edges scoped to the named callable -- which is the same domain the amended
    ``sdg_taint_query`` predicate matches against, and which both backends can answer cheaply (one
    ``DISTINCT r.var`` query on Neo4j scoped by the callable's id prefix; the adjacency the local
    backends already build, for the in-process side).

    The scoping is not cosmetic: the amended predicate matches a cut's ``var`` only on edges whose
    start node falls under the resolved callable's ``prefix`` (its ``ref``), so ``cuts`` carries
    ``{"var": ..., "prefix": ...}`` maps rather than a flat list of names -- a global cut on a
    common name like ``result`` or ``token`` would sever flows the caller never named in every
    *other* callable, over-cutting into a false refutation.

    Args:
        sanitizers: Each entry is either a bare callable name (cuts the callable) or a
            ``(name, within)`` pair (cuts the variable ``name``, scoped to callable ``within``).
        resolve_callable: A bound ``resolve_callable(name)`` -- see
            :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.resolve_callable`. Called
            with the bare name directly for a callable cut, and with ``within`` (via
            :func:`resolve_within`) for a variable cut's scope.
        edge_vars_in: Given a resolved callable's ``prefix``, the variable names present on SDG
            edges scoped to it -- the domain a variable selector is checked against (Ruling A;
            never ``resolve_value``, which addresses parameters, not the locals a real edge var
            usually is).

    Returns:
        ``(cuts, cut_callable_ids)`` -- ``cuts`` is a list of ``{"var": str, "prefix": str}`` maps,
        ready to bind as ``sdg_taint_query``'s ``$cuts``; ``cut_callable_ids`` is a list of
        ``can://`` ids, ready to bind as its ``$cut_callables``.

    Raises:
        ValueError: A pair's variable name is empty or whitespace-only. The amended predicate
            matches a cut's ``var`` against ``coalesce(r.var, '')``, so an empty string would read
            as "cut every hop with no var, in that callable" -- most control and summary edges,
            and (below the analyzer floor) every param edge too. Refused here rather than passed
            through silently.
        SelectorNotInGraph: A bare name did not resolve as a callable, a pair's ``within`` did not
            resolve as a callable, or a pair's variable does not appear on any SDG edge scoped to
            ``within``. Each resolver's own failure is the error -- there is no catch-and-retry
            under the other shape.
        AmbiguousName: A bare name, or a pair's ``within``, matched more than one callable.
    """
    cuts: List[Dict[str, str]] = []
    cut_callable_ids: List[str] = []
    for sanitizer in sanitizers:
        if isinstance(sanitizer, str):
            cut_callable_ids.append(resolve_callable(sanitizer).ref)
            continue
        name, within = sanitizer
        if not name or not name.strip():
            raise ValueError(
                f"a sanitizer variable must not be empty or whitespace-only (within={within!r}): "
                "the taint predicate reads a blank var as \"cut every hop with no var\", which "
                "would sever most control/summary edges in that callable rather than the one "
                "variable intended"
            )
        owner = resolve_within(resolve_callable, within)
        if name not in edge_vars_in(owner.ref):
            raise SelectorNotInGraph(
                "variable",
                [name],
                1,
                detail=f"no SDG edge scoped to {within!r} carries this variable; resolve_value only addresses parameters, not locals",
            )
        cuts.append({"var": name, "prefix": owner.ref})
    return cuts, cut_callable_ids
