# Dependency Analyzer Project Documentation

This document explains the internal architecture, folder structure, and step-by-step analysis workflow of the Dependency Analyzer project.

---

## 📂 Folder Structure

The project is divided into a backend Python package (`analyzer/`), a REST API server (`server.py`), and a frontend UI (`dashboard.html`).

```text
dependency-analyzer-main/
│
├── analyzer/                  # The core backend analysis engine
│   ├── __init__.py
│   ├── cli.py                 # The orchestrator. Coordinates the entire analysis flow.
│   ├── github_client.py       # Handles all communication with the GitHub API.
│   ├── package_apis.py        # Connects to PyPI and NPM to fetch latest versions/licenses.
│   ├── scorer.py              # The algorithm that calculates the 0-100 Health Score.
│   │
│   └── parsers/               # Code responsible for reading dependency manifests
│       ├── __init__.py
│       ├── base.py            # Defines the 'Dependency' data model and base interface.
│       ├── node.py            # Parses 'package.json' for JavaScript/Node.js projects.
│       └── python.py          # Parses 'requirements.txt' and 'pyproject.toml'.
│
├── server.py                  # FastAPI server providing the /api/analyze endpoints.
├── dashboard.html             # The vanilla HTML/JS/CSS frontend user interface.
├── .env                       # Stores the GITHUB_TOKEN to bypass API rate limits.
└── requirements.txt           # The analyzer's own dependencies (FastAPI, requests, etc.)
```

### Key Files & Their Responsibilities

- **`server.py`**: The web server. It serves `dashboard.html` to the browser and exposes API endpoints (like `/api/analyze`) that the frontend calls when a user clicks "Analyze". It also handles saving/loading historical scans to the disk.
- **`dashboard.html`**: The frontend. It takes the JSON results from the API and renders the beautiful gauges, score breakdown bars, Dependabot tables, and the overall conclusion messages.
- **`analyzer/cli.py`**: The brain of the operation. When an analysis starts, `cli.py` calls the GitHub Client, then the Parsers, then the Package APIs, and finally feeds everything into the Scorer.
- **`analyzer/scorer.py`**: The math engine. It contains the logic for grading version pinning, applying maintenance penalties, forgiving highly-starred libraries, and deducting points for vulnerabilities.

---

## ⚙️ Steps of Analysis

When a user submits a repository (e.g., `fastapi/fastapi`), the system goes through a strict 5-step pipeline:

### Step 1: Repository Metadata Gathering
*File responsible: `analyzer/github_client.py`*

The analyzer first contacts the GitHub API to gather top-level context about the repository. It collects:
- **`stargazers_count`**: Used to determine community trust (massive repos are forgiven for having large dependency trees).
- **`pushed_at`**: The timestamp of the last code commit. Used to determine if the project is actively maintained or abandoned.
- **`archived`**: A boolean flag indicating if the owner explicitly marked the repo as dead/read-only.
- **`license`**: The repository's open-source license, used later to check for GPL legal conflicts.

### Step 2: Traversing the Repository
*File responsible: `analyzer/github_client.py`*

The system requests the root directory structure of the repository. It looks for known dependency manifests.
- If it sees `package.json`, it knows it's a JavaScript project.
- If it sees `requirements.txt` or `pyproject.toml`, it knows it's a Python project.
- It also looks for lockfiles (`package-lock.json` or `poetry.lock`). If a lockfile is **missing**, the system assumes the repository is a *distributable library* rather than a *deployed application*, which completely changes how it is graded later.

### Step 3: Parsing Dependencies
*Files responsible: `analyzer/parsers/*.py`*

Once the manifests are found, their exact text contents are downloaded from GitHub.
The relevant parser reads the file line-by-line and extracts a list of `Dependency` objects. For each dependency, the parser figures out:
- **Name**: e.g., `requests`
- **Version Constraint**: e.g., `>=2.0.0`
- **Pinning Strategy**: The parser classifies the text into a strict category:
  - `exact` (e.g., `==2.0.0`)
  - `compatible` (e.g., `^2.0.0` or `~=2.0`)
  - `minimum` (e.g., `>=2.0`)
  - `range` (e.g., `>=1.0, <2.0`)
  - `unpinned` (e.g., `requests` with no version)
- **Environment**: Whether it is a production dependency or just a local development/testing dependency (like `pytest`).

### Step 4: External Enrichment (Analysis)
*Files responsible: `analyzer/package_apis.py` & `analyzer/github_client.py`*

The analyzer now reaches out to the broader internet to gather real-world context on the parsed dependencies:
1. **PyPI / NPM Queries**: It queries the official Python or Node registries for every single dependency to find the absolute `latest_version` released by the creators, and the software `license` it is distributed under.
2. **Dependabot Alerts**: It queries the GitHub API to see if Dependabot has flagged any known Common Vulnerabilities and Exposures (CVEs) in the repository's dependency tree.

### Step 5: The Scoring Engine
*File responsible: `analyzer/scorer.py`*

With all the data gathered, the system computes the final 0-100 Health Score.

1. **Base Score Calculation**:
   - **Pinning Quality (40 pts)**: Rewards strict version control. However, if the project is a *library* (no lockfile), it assigns perfect scores to flexible pinning like `compatible` or `minimum`.
   - **Range Tightness (20 pts)**: Rewards having boundaries on updates.
   - **Dependency Count (15 pts)**: Penalizes massive dependency trees (unless the repo has >10,000 stars, then it is forgiven).
   - **Outdated Versions (15 pts)**: Penalizes packages that are major versions behind the `latest_version` fetched from PyPI/NPM.
   - **Completeness (10 pts)**: Rewards the presence of lockfiles.
2. **Modifiers & Penalties**:
   - **Maintenance Bonus**: Adds +10 points if the repo was pushed to in the last 30 days.
   - **Abandonment Penalty**: Subtracts up to 30 points if the repo hasn't been touched in years.
   - **Archived Penalty**: Forces the Risk Level to **HIGH RISK** if GitHub marks it as archived.
   - **CVE Penalty**: Subtracts massive points for any Critical or High severity Dependabot alerts.
   - **License Penalty**: Subtracts points if a proprietary repository uses a viral GPL-licensed dependency.

The final score, breakdown, and list of enriched dependencies are packaged into a JSON payload and sent back to `dashboard.html` for rendering!
