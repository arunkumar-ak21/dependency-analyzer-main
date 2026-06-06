# Dependency Analyzer: Scoring & Evaluation Guide

This document explains exactly how a repository's final Health Score (0-100) is calculated. 

The grading system is broken down into two main phases:
1. **The Base Score** (out of 100 points)
2. **Contextual Modifiers & Penalties** (additions or deductions based on real-world risks)

---

## 1. The Base Score Categories (100 Points Total)

Every dependency in the repository is evaluated across five distinct categories. 

> [!NOTE]
> **Production vs Development Weights**
> Dependencies meant for production (e.g., `react`) are weighted **2x heavier** than development dependencies (e.g., `pytest`). A badly pinned testing framework hurts the score less than a badly pinned production framework.

### A. Version Pinning Quality (Max 40 Points)
This checks if the developer has explicitly told the system which version of a package to install.
- **Exact (1.0)**: `==2.0.0` (Perfect pinning, maximum points)
- **Compatible (0.8)**: `~=2.0` or `^2.0` (Good pinning, allows safe bug fixes)
- **Range (0.6)**: `>=1.0, <2.0` (Acceptable, but wider boundaries)
- **Minimum (0.3)**: `>=2.0` (Poor pinning, allows breaking major updates)
- **Unpinned (0.0)**: `requests` (Terrible, installs whatever the newest version is, causing guaranteed breakages eventually)

### B. Version Range Tightness (Max 20 Points)
This measures how tight the allowed installation boundaries are.
- To earn points here, the pinning strategy must be **"Exact"** or **"Compatible"**.
- If a dependency uses an open-ended boundary (like `Minimum` or `Unpinned`), it receives 0 points for tightness.

### C. Dependency Count Risk (Max 15 Points)
The more dependencies a project has, the higher the mathematical chance of a supply chain attack or bug.
- **<= 10 dependencies**: Perfect score (15 pts)
- **<= 30 dependencies**: Minor penalty (13.5 pts)
- **<= 60 dependencies**: Moderate penalty (10.5 pts)
- **<= 100 dependencies**: High penalty (7.5 pts)
- **> 100 dependencies**: Severe penalty (4.5 pts)

### D. Outdated Version Flags (Max 15 Points)
The analyzer queries PyPI or NPM for the absolute latest version of every dependency.
- **Pre-1.0 Software**: If a dependency is on version `0.x.x`, it receives a minor penalty because the software API is considered unstable.
- **Major Versions Behind**: If the repository is using `v2.0` but the registry says the latest version is `v4.0`, the system subtracts points for every major version it is behind. Software that is severely outdated is highly susceptible to unpatched vulnerabilities.

### E. Manifest Completeness (Max 10 Points)
This checks for the presence of a "Lock File" (e.g., `package-lock.json`, `poetry.lock`). 
- If a lock file is present, the project receives a perfect 10/10. Lock files guarantee deterministic, repeatable builds.
- If it is missing, the project only gets 5/10.

---

## 2. Context-Aware Library Forgiveness

The Base Score logic assumes the repository is a deployed application. However, **distributable libraries** (like React, Django, or FastAPI) shouldn't use exact versions or lock files, because doing so prevents users from installing other packages (Dependency Hell).

If the system detects that the repository is a library (no lock file is present):
- **Compatible, Minimum, and Range** pinning strategies are immediately upgraded to receive **Perfect 1.0** scores in both Pinning Quality and Range Tightness. 
- This prevents standard open-source libraries from being falsely flagged as high-risk.

---

## 3. Modifiers & Penalties

After the Base Score is calculated out of 100, the system looks at external context from GitHub and Dependabot to apply real-world bonuses and penalties.

### 🌟 Reputation & Community Trust Bonus
Massive repositories (like Next.js) naturally have hundreds of dependencies. 
- If a repository has over **1,000 GitHub Stars**, the Dependency Count Penalty is cut in half.
- If a repository has over **10,000 GitHub Stars**, the Dependency Count Penalty is completely erased. The massive community oversight offsets the risk of a deep dependency tree.

### 🛠️ Maintenance Activity
Dependencies are inherently dangerous if the maintainers abandon the project.
- **Active Bonus**: If a commit was pushed to the repo in the last 30 days, the repo receives a **+10 point bonus** (capped at 100 total).
- **Stale Penalty**: If no commit was pushed in >6 months, it loses 5 points.
- **Abandoned Penalty**: If no commit was pushed in >1 year (-15 pts), >2 years (-30 pts), or >3 years (-30 pts).

### ☠️ Severe Penalties & Risk Overrides

Some risks are so severe that they tank the mathematical score entirely:

1. **Vulnerability (CVE) Penalty**:
   - Critical Dependabot Alert: -15 points per alert
   - High Dependabot Alert: -8 points per alert
   - Medium Dependabot Alert: -3 points per alert
2. **License Conflict Penalty**:
   - If the repository is proprietary/closed-source, but it imports a dependency licensed under a viral `GPL` license, the system deducts a flat **10 points** to highlight the massive legal risk.
3. **Archived / Officially Dead Override**:
   - If GitHub explicitly marks the repository as "Archived" (read-only), a minimum **-30 point penalty** is applied.
   - Furthermore, the Final Risk Level is hardcoded to **HIGH RISK**, regardless of how high the base score was.

---

## 4. Final Risk Level

After all math is resolved, the final score dictates the Risk Level badge displayed on the dashboard:
- **80 – 100 points**: 🟢 LOW RISK
- **50 – 79 points**: 🟡 MEDIUM RISK
- **0 – 49 points**: 🔴 HIGH RISK
