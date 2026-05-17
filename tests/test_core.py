"""Test core functionalities of the build process."""

import glob
from pdb import set_trace
from pathlib import Path
import re
from subprocess import run
import zipfile

import pytest


@pytest.fixture(scope="session")
def build_project():
    result = run(["uv", "build"], capture_output=True, text=True)
    assert result.returncode == 0, f"uv build failed: {result.stderr}"

    # Find the .whl file in the dist directory
    wheel_files = glob.glob("dist/*.whl")
    assert wheel_files, "No .whl file found in the dist directory"
    return wheel_files[0]


def test_codeanalyzer_jar_in_wheel(build_project):
    wheel_path = build_project
    jar_pattern = re.compile(r"cldk/analysis/java/codeanalyzer/jar/codeanalyzer-.*\.jar")

    # Open the .whl file as a zip archive and check for the jar file
    with zipfile.ZipFile(wheel_path, "r") as wheel_zip:
        jar_files = [file for file in wheel_zip.namelist() if jar_pattern.match(file)]

    assert jar_files, "codeanalyzer-*.jar file not found in the wheel package"
