################################################################################
# Copyright IBM Corporation 2024
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

"""
Global Test Fixtures
"""

import os

os.putenv("ASAN_DISABLE", "1")
os.putenv("ASAN_OPTIONS", "verify_asan_link_order=0")

import json
from pdb import set_trace
import shutil
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

# third part imports
import toml
import pytest


@pytest.fixture(scope="session", autouse=True)
def analysis_json_fixture():
    """Fixture to get the path of the analysis.json file for testing

    Returns:
        Path: The folder that contains the analysis.json file
    """
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    return Path(config["tool"]["cldk"]["testing"]["sample-application-analysis-json"]) / "slim"


@pytest.fixture(scope="session", autouse=True)
def analysis_json(analysis_json_fixture) -> str:
    """Opens the analysis.json file and returns the contents as a json string"""
    json_file = {}
    # Read the json file and return it as a json string
    with open(os.path.join(analysis_json_fixture, "analysis.json"), "r", encoding="utf-8") as json_data:
        json_file = json.dumps(json.load(json_data))

    return json_file


@pytest.fixture(scope="session", autouse=True)
def codeanalyzer_jar_path():
    """Fixture to get the path to the codeanalyzer.jar file

    Returns:
        Path: The path to the codeanalyzer.jar file
    """
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    return Path(config["tool"]["cldk"]["testing"]["codeanalyzer-jar-path"]) / "2.3.0"


@pytest.fixture(scope="session", autouse=True)
def test_fixture():
    """
    Returns the path to the test data directory.

    Yields:
        Path : The path to the test data directory.
    """
    # ----------------------------------[ SETUP ]----------------------------------
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    # Access the test data path
    test_data_path = config["tool"]["cldk"]["testing"]["sample-application"]
    filename = Path(test_data_path).absolute() / "daytrader8-1.2.zip"

    # If the file doesn't exist, download it
    if not Path(filename).exists():
        # If the path doesn't exist, create it
        if not Path(test_data_path).exists():
            Path(test_data_path).mkdir(parents=True)
        url = "https://github.com/OpenLiberty/sample.daytrader8/archive/refs/tags/v1.2.zip"
        urlretrieve(url, filename)

    # Extract the zip file to the test data path
    with zipfile.ZipFile(filename, "r") as zip_ref:
        zip_ref.extractall(test_data_path)

    # --------------------------------------------------------------------------------
    # Daytrader8 sample application path
    yield Path(test_data_path) / "sample.daytrader8-1.2"

    # -----------------------------------[ TEARDOWN ]----------------------------------
    # Remove the daytrader8 sample application that was downloaded for testing
    for directory in Path(test_data_path).iterdir():
        if directory.exists() and directory.is_dir():
            shutil.rmtree(directory)
    # ---------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def test_fixture_pbw():
    """
    Returns the path to the test data directory for plantsbywebsphere.

    Yields:
        Path : The path to the test data directory.
    """
    # ----------------------------------[ SETUP ]----------------------------------
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    # Access the test data path
    test_data_path = config["tool"]["cldk"]["testing"]["sample-application"]
    filename = Path(test_data_path).absolute() / "plantsbywebsphere.zip"

    # If the file doesn't exist, raise an error
    if not Path(filename).exists():
        raise FileNotFoundError(f"File {filename} does not exist. Please download the file and try again")

    # Extract the zip file to the test data path
    with zipfile.ZipFile(filename, "r") as zip_ref:
        zip_ref.extractall(test_data_path + "plantsbywebsphere")

    # Make chmod +x on the gradlew file
    gradlew_path = Path(test_data_path) / "plantsbywebsphere" / "gradlew"
    os.chmod(gradlew_path, 0o755)
    # --------------------------------------------------------------------------------
    # Daytrader8 sample application path
    yield Path(test_data_path) / "plantsbywebsphere"

    # -----------------------------------[ TEARDOWN ]----------------------------------
    # Remove the daytrader8 sample application that was downloaded for testing
    for directory in Path(test_data_path).iterdir():
        if directory.exists() and directory.is_dir():
            shutil.rmtree(directory)
    # ---------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def test_fixture_binutils():
    """Create a test fixture for the analysis of C applications"""
    # ----------------------------------[ SETUP ]----------------------------------
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    # Access the test data path
    test_data_path = config["tool"]["cldk"]["testing"]["sample-c-application"]
    filename = Path(test_data_path).absolute() / "binutils.zip"

    # Extract the zip file to the test data path
    with zipfile.ZipFile(filename, "r") as zip_ref:
        zip_ref.extractall(test_data_path)

    # ------------------------------ [ TEST FIXTURE ]------------------------------
    # Binutils sample application path
    yield Path(test_data_path) / "binutils"

    # ---------------------------------[ TEARDOWN ]--------------------------------
    # Remove the binutils sample application that was downloaded for testing
    for directory in Path(test_data_path).iterdir():
        if directory.exists() and directory.is_dir():
            shutil.rmtree(directory)

@pytest.fixture(scope="session", autouse=True)
def java_code() -> str:
    """
    Returns sample Java source code for analysis.

    Yields:
        str : Java code to be analyzed.
    """
    # ----------------------------------[ SETUP ]----------------------------------
    # Path to your pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Load the configuration
    config = toml.load(pyproject_path)

    # Access the test data path
    test_data_path = config["tool"]["cldk"]["testing"]["sample-application"]
    javafile = Path(test_data_path).absolute() / ("WeatherServlet.java")
    with open(javafile) as f:
        return f.read()
