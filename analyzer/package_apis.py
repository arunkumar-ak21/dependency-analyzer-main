"""
External Package APIs
=====================
Client for querying PyPI and npm registries to fetch the true latest
versions and licenses for dependencies.

Uses ThreadPoolExecutor to fetch multiple dependencies in parallel,
avoiding the massive slowdown of sequential HTTP requests.
"""

import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .parsers.base import Dependency

logger = logging.getLogger(__name__)

# Constants
PYPI_URL = "https://pypi.org/pypi/{}/json"
NPM_URL = "https://registry.npmjs.org/{}/latest"
MAX_WORKERS = 10  # Balance between speed and not overwhelming the registries


class PackageAPIClient:
    """Client for fetching package metadata from PyPI and npm."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "RepoDepAnalyzer/3.0",
        })

    def get_pypi_info(self, package_name: str) -> dict:
        """Fetch latest version and license from PyPI."""
        url = PYPI_URL.format(package_name)
        try:
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("info", {})
                lic = data.get("license", "unknown")
                if lic and len(lic) > 50:
                    lic = lic[:47] + "..."
                return {
                    "latest_version": data.get("version", ""),
                    "license": lic or "unknown"
                }
        except requests.RequestException as exc:
            logger.debug("PyPI lookup failed for %s: %s", package_name, exc)
        return {"latest_version": "", "license": "unknown"}

    def get_npm_info(self, package_name: str) -> dict:
        """Fetch latest version and license from npm."""
        # Replace '/' with '%2F' for scoped packages (e.g., @babel/core)
        safe_name = package_name.replace("/", "%2F")
        url = NPM_URL.format(safe_name)
        try:
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                license_data = data.get("license", "unknown")
                if isinstance(license_data, dict):
                    license_data = license_data.get("type", "unknown")
                if license_data and len(license_data) > 50:
                    license_data = license_data[:47] + "..."
                return {
                    "latest_version": data.get("version", ""),
                    "license": license_data or "unknown"
                }
        except requests.RequestException as exc:
            logger.debug("npm lookup failed for %s: %s", package_name, exc)
        return {"latest_version": "", "license": "unknown"}

    def fetch_dependency_metadata(self, deps: list[Dependency], ecosystems: dict) -> None:
        """
        Augment a list of Dependency objects IN-PLACE with latest version and license.
        Uses a thread pool to process dependencies concurrently.
        """
        # Determine ecosystem for each dependency based on source file
        # ecosystems format: {"python": {"manifest_files": ["requirements.txt"]}}
        file_to_eco = {}
        for eco_name, info in ecosystems.items():
            for f in info.get("manifest_files", []):
                file_to_eco[f] = eco_name
            for f in info.get("lock_files", []):
                file_to_eco[f] = eco_name

        def fetch_single(dep: Dependency):
            eco = file_to_eco.get(dep.source_file, "")
            if eco == "python":
                info = self.get_pypi_info(dep.name)
            elif eco == "javascript":
                info = self.get_npm_info(dep.name)
            else:
                return

            dep.latest_version = info["latest_version"]
            dep.license = info["license"]

        # Run in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            futures = [executor.submit(fetch_single, d) for d in deps]
            # Wait for completion (don't strictly care about results as it modifies in-place)
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.debug("Error during concurrent lookup: %s", exc)

