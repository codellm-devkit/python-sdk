import os
from unittest import mock

import pytest

import cldk.analysis.java.codeanalyzer.codeanalyzer as _codeanalyzer


@pytest.fixture(autouse=True)
def _no_jdk_for_mocked_analyzer(monkeypatch, tmp_path):
    """Tests that patch ``subprocess.run`` never launch a JVM, so do not resolve one for them.

    ``_get_codeanalyzer_exec`` calls ``ensure_jdk`` before every analyzer run; without a
    ``$JAVA_HOME`` that carries ``jmods`` that is a Temurin download into a fresh cache dir
    (#328). Tests that run the real jar (``subprocess.run`` unpatched) still get the real lookup.
    ``JAVA_HOME`` is restored afterwards because ``_get_codeanalyzer_exec`` exports it.
    """
    real_ensure_jdk = _codeanalyzer.ensure_jdk

    def ensure_jdk(java_cache_dir):
        if isinstance(_codeanalyzer.subprocess.run, mock.Mock):
            return tmp_path / "jdk"
        return real_ensure_jdk(java_cache_dir)

    monkeypatch.setattr(_codeanalyzer, "ensure_jdk", ensure_jdk)
    if "JAVA_HOME" in os.environ:
        monkeypatch.setenv("JAVA_HOME", os.environ["JAVA_HOME"])
    else:
        monkeypatch.delenv("JAVA_HOME", raising=False)
