<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/codellm-devkit/.github/main/profile/assets/cldk-dark.png">
  <img src="https://raw.githubusercontent.com/codellm-devkit/.github/main/profile/assets/cldk-light.png" alt="Codellm-Devkit logo">
</picture>

<p align='center'>
  <a href="https://arxiv.org/abs/2410.13007">
    <img src="https://img.shields.io/badge/arXiv-2410.13007-b31b1b?style=for-the-badge" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge" />
  </a>
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img src="https://img.shields.io/badge/License-Apache%202.0-green?style=for-the-badge" />
  </a>
  <a href="https://codellm-devkit.info">
    <img src="https://img.shields.io/badge/Docs-codellm--devkit.info-blue?style=for-the-badge" />
  </a>
  <a href="https://badge.fury.io/py/cldk">
    <img src="https://img.shields.io/pypi/v/cldk?style=for-the-badge&label=cldk&color=blue" />
  </a>
  <a href="https://discord.gg/zEjz9YrmqN">
    <img src="https://dcbadge.limes.pink/api/server/https://discord.gg/zEjz9YrmqN?style=for-the-badge"/>
  </a>
</p>

# Codellm-Devkit (CLDK)

CLDK is a Python SDK that runs static program analysis on Java, Python, and TypeScript code. AI agents and LLM applications query code in all three languages through one API. Support for other languages is in development. [Krishna et al. (2025)](https://doi.org/10.1145/3696630.3728555) describe the design and how it supplies program-analysis context to LLMs that work on code.

CLDK unifies language-specific static-analysis tools, formats, and program models so agents need not reconcile them. Each analyzer emits one shared schema. The SDK maps that schema onto typed [Pydantic](https://docs.pydantic.dev/) models. The agent queries those models through the API. The API answers the questions an agent asks while it reads, tests, or changes code:

- Which callable contains this line?
- What calls it, and what does it call?
- How does a value move through the program?
- Which callables are entry points?
- Which packages does the project depend on?
- Which code reads a configuration key?

Two backends answer every query. The local backend runs the analyzer. The Neo4j backend reads a graph the analyzer wrote earlier and never runs the analyzer. Both give the same answer. Where a backend cannot answer, it refuses and says why. It never returns an empty value that reads like a real answer.

CLDK is developed at IBM Software Innovation Labs. Issues and contributions are welcome.

## Installation

CLDK requires Python 3.11 or newer.

```bash
pip install cldk             # Python and TypeScript analysis
pip install "cldk[java]"     # adds the Java analyzer: the jar and a bundled JVM
pip install "cldk[neo4j]"    # adds the read-only Neo4j backend
pip install "cldk[all]"      # everything above
```

**Java call graphs need a JDK.** The bundled JVM is enough for the symbol table. From the call-graph level up, the analyzer compiles the project with its Maven or Gradle wrapper. `JAVA_HOME` must then point to a JDK with `javac` (Java 11 or newer). Without one, the analyzer still exits 0 but emits a call graph with declared edges only. CLDK logs the degradation at `WARNING`.

```bash
export JAVA_HOME=/path/to/jdk   # must contain bin/javac
```

**Upgrading from 1.x.** The old entry point `CLDK(language="java").analysis(...)` still works. It emits a `DeprecationWarning`. Use the factory methods.

## Quick Start

The three steps below take a Python project from disk to a question about its code. Java and TypeScript facades answer the same calls, so the same steps apply with a different factory.

1. Create a facade. Each factory returns a typed analysis facade for its language. The call-graph level covers all three steps.

   ```python
   from cldk import CLDK
   from cldk.analysis import AnalysisLevel

   analysis = CLDK.python(
       project_path="/path/to/python/project",
       analysis_level=AnalysisLevel.call_graph,
   )
   # CLDK.java(...) and CLDK.typescript(...) take the same arguments.
   ```

2. Find the callable at a line, and read its source.

   ```python
   hit = analysis.locate("src/app/handlers.py", line=42)
   print(hit.callable.signature if hit.callable else "module scope")
   print(hit.source)   # the enclosing callable's text
   ```

3. If the line was inside a callable, ask who calls it.

   ```python
   for caller in analysis.callers_of(hit.callable.signature):
       print(caller.callable, caller.file, caller.line)
   ```

[Query Surface](#query-surface) lists what else a facade answers. [Backends](#backends) shows how to read a graph from Neo4j instead of running the analyzer.

## Query Surface

Every family below is on all three facades, except where the table says otherwise. The [agent API reference](./docs/agent-api-reference.md) names each accessor with its return type, its cost, and what will mislead you.

| Family | Question it answers |
| --- | --- |
| Addressing | Which callable owns this line? What does this name resolve to? |
| Symbol table | What does the project declare? |
| Declarations by kind | Which interfaces, enums, records, type aliases, exports, or functions exist? (Java and TypeScript, with a different set on each) |
| Call graph | What calls this, and what does it call? |
| Per-callable graphs | How do control and data flow inside one callable? |
| Slices and paths | What does this value depend on, and where does it flow? |
| Taint | Do these sources reach these sinks? You supply the names. |
| Entry points | Where does execution enter the application? |
| Bulk projections | What are the callables, their bodies, decorators, and call sites, in one pass? |
| Repository artifacts | What does the project depend on, and which code reads which configuration key? |
| View dispatches | Which code renders which template? (Java only) |
| Comments | What do the comments and docstrings say? (Java only) |

Deeper questions need a higher analysis level. The default level is the symbol table. Pass `analysis_level=` to raise it, as in Quick Start step 1. Where a backend or a language cannot answer, the accessor refuses rather than returning an empty result. The reference lists every such case.

## Backends

The type of the `backend=` configuration selects the backend. `CodeAnalyzerConfig` and its subclasses run the analyzer locally. This is the default. `Neo4jConnectionConfig` reads a graph the analyzer emitted earlier with `--emit neo4j`, so no source checkout is needed:

```python
from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

analysis = CLDK.python(
    backend=Neo4jConnectionConfig(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="...",
        application_name="my-app",  # the --app-name the graph was loaded with
    ),
)
```

| Language | Analyzer | Built on | Runs as |
| --- | --- | --- | --- |
| Java | [`codeanalyzer-java`](https://github.com/codellm-devkit/codeanalyzer-java) | JavaParser and WALA | a subprocess on the bundled JVM |
| Python | [`codeanalyzer-python`](https://github.com/codellm-devkit/codeanalyzer-python) | Jedi, the standard-library `ast`, and a vendored Scalpel alias oracle | in-process |
| TypeScript / JavaScript | [`codeanalyzer-typescript`](https://github.com/codellm-devkit/codeanalyzer-typescript) | the TypeScript compiler via ts-morph, Rapid Type Analysis, and a def-use linker | a self-contained binary |

The reference's [Attach](./docs/agent-api-reference.md#attach) section and the per-language sections after it list the graph versions each backend accepts. They also show how to migrate an older graph.

## Read Next

- [docs/agent-api-reference.md](./docs/agent-api-reference.md) is the full reference. It opens with the four moves most questions decompose into, then lists every accessor with its cost.
- [docs/skills/using-cldk/SKILL.md](./docs/skills/using-cldk/SKILL.md) is a Claude Code skill. It states the invariants an agent must hold. Ids are opaque, levels gate accessors, bounds are never silent, and a refusal is not an empty answer. To install it, copy the directory into your skills folder.
- [docs/architecture.md](./docs/architecture.md) shows how the SDK is laid out, for contributors.
- [codellm-devkit.info](https://codellm-devkit.info) holds the full documentation.

## Contributing

We welcome contributors of all experience levels. See the [CONTRIBUTING](./CONTRIBUTING.md) guide to get started.

## Citation

If you use CLDK in your research, please cite:

```bibtex
@inproceedings{krishna2025codellm,
  title     = {Codellm-Devkit: A Framework for Contextualizing Code LLMs with Program Analysis Insights},
  author    = {Krishna, Rahul and Pan, Rangeet and Pavuluri, Raju and Tamilselvam, Srikanth and Vukovic, Maja and Sinha, Saurabh},
  booktitle = {Proceedings of the 33rd ACM International Conference on the Foundations of Software Engineering (FSE Companion '25)},
  pages     = {308--318},
  year      = {2025},
  publisher = {ACM},
  doi       = {10.1145/3696630.3728555}
}
```

Research that uses or cites CLDK is listed in [docs/citations.md](./docs/citations.md).

## Maintainers

| Name | Email |
| --- | --- |
| Rahul Krishna | [i.m.ralk@gmail.com](mailto:i.m.ralk@gmail.com) |
| Rangeet Pan | [rangeet.pan@ibm.com](mailto:rangeet.pan@ibm.com) |
| Saurabh Sinha | [sinhas@us.ibm.com](mailto:sinhas@us.ibm.com) |

Licensed under the [Apache License 2.0](./LICENSE).
