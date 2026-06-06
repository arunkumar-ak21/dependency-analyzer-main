"""
Node.js Dependency Parser
==========================
Parses ``package.json`` to extract ``dependencies``,
``devDependencies``, ``peerDependencies``, and
``optionalDependencies``.
"""

import json
import logging

from .base import BaseParser, Dependency

logger = logging.getLogger(__name__)


class NodeParser(BaseParser):
    ecosystem = "node"

    # Sections in package.json that contain dependency mappings.
    _SECTIONS = [
        ("dependencies", False),
        ("devDependencies", True),
        ("peerDependencies", False),
        ("optionalDependencies", False),
    ]

    def parse(self, content: str, filename: str) -> list[Dependency]:
        """
        Parse a ``package.json`` file.

        Each dependency entry is a simple ``"name": "version_range"`` mapping.
        Versions can be semver ranges (``^1.2.3``), exact (``1.2.3``), URLs,
        or tags (``latest``, ``next``).
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", filename, exc)
            return []

        if filename.endswith("package-lock.json"):
            return self._parse_package_lock(data, filename)

        deps: list[Dependency] = []

        for section_key, is_dev in self._SECTIONS:
            section = data.get(section_key, {})
            if not isinstance(section, dict):
                continue
            for name, version in section.items():
                version = str(version).strip()
                # URLs (git+, http://) → treat as unpinned
                if "://" in version or version.startswith("git+"):
                    constraint = ""
                    pinning = "unpinned"
                else:
                    constraint = version
                    pinning = self._classify_node_version(version)

                deps.append(Dependency(
                    name=name,
                    version_constraint=constraint,
                    source_file=filename,
                    pinning_type=pinning,
                    is_dev=is_dev,
                ))

        logger.info("Parsed %d dependencies from %s", len(deps), filename)
        return deps

    # ------------------------------------------------------------------
    # Node-specific version classification
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_node_version(v: str) -> str:
        """
        Classify npm-style semver ranges.

        Examples
        --------
        ``"1.2.3"``   → exact
        ``"^1.2.3"``  → compatible
        ``"~1.2.3"``  → compatible
        ``">=1.0.0"`` → minimum
        ``">=1 <2"``  → range
        ``"*"``       → unpinned
        ``"latest"``  → unpinned
        """
        v = v.strip()
        if not v or v == "*" or v.lower() in ("latest", "next", "canary"):
            return "unpinned"
        if v.startswith("^") or v.startswith("~"):
            return "compatible"
        if ">=" in v and "<" in v:
            return "range"
        if v.startswith(">=") or v.startswith(">"):
            return "minimum"
        if v[0].isdigit():
            return "exact"
        return "complex"

    def _parse_package_lock(self, data: dict, filename: str) -> list[Dependency]:
        """
        Parse a package-lock.json (v1, v2, v3).
        Extracts dependencies and marks them as transitive.
        The orchestrator will merge these with direct dependencies.
        """
        deps: list[Dependency] = []
        
        # v2/v3 use "packages"
        if "packages" in data:
            for path, info in data["packages"].items():
                if not path:
                    continue  # skip the root project itself
                name = path.split("node_modules/")[-1]
                version = info.get("version", "")
                if not name or not version:
                    continue
                deps.append(Dependency(
                    name=name,
                    version_constraint=version,
                    source_file=filename,
                    pinning_type="exact", # Lockfiles have exact versions
                    is_dev=info.get("dev", False),
                    is_transitive=True
                ))
        # v1 uses "dependencies"
        elif "dependencies" in data:
            for name, info in data["dependencies"].items():
                version = info.get("version", "")
                if not name or not version:
                    continue
                deps.append(Dependency(
                    name=name,
                    version_constraint=version,
                    source_file=filename,
                    pinning_type="exact",
                    is_dev=info.get("dev", False),
                    is_transitive=True
                ))
                
        logger.info("Parsed %d transitive dependencies from %s", len(deps), filename)
        return deps
