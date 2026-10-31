"""Regenerate identity-v2 positive snapshots from source; never translate persisted IDs.

Run with the SDK Python environment after configuring the reviewed local producer artifacts:
    python tests/resources/regenerate_identity_fixtures.py [all|java|typescript|go]
Java requires Maven, a JDK, and dependency access. Each producer invocation is bounded to 180s.
"""

from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

RESOURCES = Path(__file__).resolve().parent


def run(argv: list[str], log: Path) -> None:
    print(f"Generating {log.stem}", flush=True)
    with log.open("w") as stream:
        subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)


def generate_typescript(work: Path) -> None:
    binary = os.environ["CODEANALYZER_TS_BIN"]
    import shlex

    for level in [1, 2, 3, 4]:
        out = work / f"ts-a{level}"
        run(
            shlex.split(binary)
            + [
                "-i",
                str(RESOURCES / "typescript/application"),
                "--namespace=slim",
                "--app-name=slim",
                "-a",
                str(level),
                "-o",
                str(out),
                "--cache-dir",
                str(work / f"ts-cache-a{level}"),
                "--skip-tests",
            ],
            work / f"ts-a{level}.log",
        )
        shutil.copyfile(out / "analysis.json", RESOURCES / f"typescript/analysis_json/v2/a{level}/analysis.json")


def generate_go(work: Path) -> None:
    import shlex

    with zipfile.ZipFile(RESOURCES / "go/litestream.zip") as archive:
        archive.extractall(work / "go-source")
    out = work / "go-output"
    run(
        shlex.split(os.environ["CODEANALYZER_GO_BIN"])
        + [
            "-i",
            str(work / "go-source/litestream"),
            "--namespace=litestream",
            "--app-name=litestream",
            "-a",
            "2",
            "-o",
            str(out),
            "--cache-dir",
            str(work / "go-cache"),
            "--skip-tests",
        ],
        work / "go.log",
    )
    shutil.copyfile(out / "analysis.json", RESOURCES / "go/analysis.json")


def generate_java(work: Path) -> None:
    import codeanalyzer_java

    if shutil.which("mvn") is None:
        raise RuntimeError("Maven is required to regenerate the compiled Java snapshots")
    with zipfile.ZipFile(RESOURCES / "java/application/daytrader8-1.2.zip") as archive:
        archive.extractall(work / "java-source")
    source = work / "java-source/sample.daytrader8-1.2"

    def analyze(name: str, root: Path, level: int, extra: list[str]) -> Path:
        output = work / name
        run(
            codeanalyzer_java.command()
            + ["-i", str(root), "--namespace=daytrader8", "--app-name=daytrader8", "-a", str(level), "-o", str(output), "-c", str(work / (name + "-cache")), "-v"]
            + extra,
            work / (name + ".log"),
        )
        return output / "analysis.json"

    # Build the complete application's classes first. Strict rejects engine-free degraded L4.
    analyze("java-a4-full", source, 4, ["--strict"])
    a1 = analyze("java-a1", source, 1, [])
    pruned = work / "java-pruned"
    shutil.copytree(source, pruned)
    keep = {"beans/MarketSummaryDataBean.java", "beans/RunStatsDataBean.java", "impl/direct/TradeDirect.java", "web/servlet/TradeServletAction.java"}
    for path in (pruned / "src/main/java").rglob("*.java"):
        if not any(path.as_posix().endswith("/" + key) for key in keep):
            path.unlink()
    a4 = analyze("java-a4", pruned, 4, ["--no-build", "--strict"])
    for level, payload in [(1, a1), (4, a4)]:
        (RESOURCES / f"java/analysis_json/v2/a{level}/analysis.json.gz").write_bytes(gzip.compress(payload.read_bytes(), mtime=0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=["all", "java", "typescript", "go"], default="all", nargs="?")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cldk-identity-fixtures-") as temporary:
        work = Path(temporary)
        for language, generate in [("typescript", generate_typescript), ("go", generate_go), ("java", generate_java)]:
            if args.language in ["all", language]:
                generate(work)


if __name__ == "__main__":
    main()
