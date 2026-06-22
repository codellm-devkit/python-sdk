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

"""Fetch + cache a self-contained Temurin JDK to run codeanalyzer.jar.

Mirrors the codeanalyzer-python ``CodeQLLoader`` pattern (download a platform
archive from a release, extract restoring exec bits, locate the binary), adapted
for a JDK:

  * pinned to an exact Temurin release (reproducible) instead of "latest",
  * SHA256-verified,
  * downloads the **JDK** (not a JRE) so ``jmods/`` is present -- WALA needs it for
    call-graph (``-a 2``) analysis (``ScopeUtils`` walks ``$JAVA_HOME/jmods``),
  * cached under the backend's existing per-language cache dir
    (``<cache_dir>/java/jdk/``; ``cache_dir`` defaults to ``<project>/.codeanalyzer``
    -- the same root every cldk backend uses via ``cache_subdir``). It is **not** a
    new cache location; share it across projects by passing a common ``cache_dir``.

A bundled JDK is too large for the PyPI wheel (~180 MB compressed, per platform),
so it is fetched once on first use rather than shipped. Running on a real HotSpot
JVM gives full analysis fidelity (unlike the GraalVM native image).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Pinned Temurin release. Bump deliberately; lives in code so it is available at
# runtime (pyproject.toml is not installed into site-packages).
JDK_RELEASE = "jdk-21.0.5+11"


class JdkLoader:
    """Resolve a Temurin JDK from the Adoptium API, mirroring CodeQLLoader."""

    _API = "https://api.adoptium.net/v3"

    @classmethod
    def _os_arch(cls) -> tuple[str, str]:
        system = {"Linux": "linux", "Darwin": "mac", "Windows": "windows"}.get(platform.system())
        arch = {"x86_64": "x64", "amd64": "x64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
            platform.machine().lower()
        )
        if not system or not arch:
            raise RuntimeError(f"Unsupported platform: {platform.system()} / {platform.machine()}")
        return system, arch

    @classmethod
    def _resolve_asset(cls) -> tuple[str, str]:
        """Return ``(download_url, sha256)`` for the pinned JDK binary."""
        os_, arch = cls._os_arch()
        url = (
            f"{cls._API}/assets/version/{JDK_RELEASE}"
            f"?os={os_}&architecture={arch}&image_type=jdk"
            f"&jvm_impl=hotspot&heap_size=normal&vendor=eclipse"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "cldk"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        if not data:
            raise RuntimeError(f"No Temurin {JDK_RELEASE} build for {os_}/{arch}")
        pkg = data[0]["binaries"][0]["package"]
        return pkg["link"], pkg["checksum"]

    @classmethod
    def _java_home(cls, root: Path) -> Path:
        """The extracted dir with both ``bin/java`` and ``jmods`` (mac: ``.../Contents/Home``)."""
        exe = "java.exe" if os.name == "nt" else "java"
        for java in root.rglob(exe):
            home = java.parent.parent
            if java.parent.name == "bin" and (home / "jmods").is_dir():
                return home.resolve()
        raise FileNotFoundError("no JDK-with-jmods found in the extracted archive")

    @classmethod
    def download_and_extract(cls, dest: Path) -> Path:
        url, sha = cls._resolve_asset()
        dest.mkdir(parents=True, exist_ok=True)
        archive = dest / url.split("/")[-1]

        logger.info(f"Downloading Temurin {JDK_RELEASE} from {url}")
        digest = hashlib.sha256()
        req = urllib.request.Request(url, headers={"User-Agent": "cldk"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(archive, "wb") as f:
            for chunk in iter(lambda: resp.read(1 << 16), b""):
                f.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != sha:
            archive.unlink(missing_ok=True)
            raise RuntimeError(f"JDK checksum mismatch: {digest.hexdigest()} != {sha}")

        logger.info(f"Extracting JDK to {dest}")
        if archive.name.endswith(".zip"):
            # zipfile.extractall drops the executable bit; copy each stored mode
            # back (same fix the CodeQL loader applies).
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    out = zf.extract(info, dest)
                    mode = info.external_attr >> 16
                    if mode:
                        os.chmod(out, mode)
        else:
            with tarfile.open(archive) as tf:  # tar preserves modes
                tf.extractall(dest)
        archive.unlink(missing_ok=True)

        java_home = cls._java_home(dest)
        java = java_home / "bin" / ("java.exe" if os.name == "nt" else "java")
        st = java.stat()
        java.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return java_home


def ensure_jdk(java_cache_dir: Path) -> Path:
    """Return ``JAVA_HOME`` for a JDK with ``jmods``.

    Args:
        java_cache_dir: the backend's java cache dir (e.g. ``<project>/.codeanalyzer/java``,
            from :func:`cldk.analysis.commons.backend_config.cache_subdir`). The JDK is
            cached at ``<java_cache_dir>/jdk/<release>/`` -- the existing per-language
            cache root, not a new location.

    Resolution order (mirrors codeanalyzer-python's ``_ensure_codeql_bin``):
      1. the cached JDK under ``<java_cache_dir>/jdk/<release>/`` -- reused across runs;
      2. a system ``$JAVA_HOME`` that actually has ``jmods`` -- honored verbatim;
      3. otherwise download + extract the pinned Temurin JDK into the cache.
    """
    home = Path(java_cache_dir) / "jdk" / JDK_RELEASE
    java = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    if java.exists() and (home / "jmods").is_dir():
        logger.debug(f"Reusing cached JDK at {home}")
        return home

    sys_home = os.environ.get("JAVA_HOME")
    if sys_home and (Path(sys_home) / "jmods").is_dir():
        logger.debug(f"Using system JDK (has jmods) at {sys_home}")
        return Path(sys_home)

    logger.info(f"JDK with jmods not found; downloading into {home}.")
    return JdkLoader.download_and_extract(home)
