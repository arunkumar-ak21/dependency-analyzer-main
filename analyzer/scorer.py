"""
Health Scorer
=============
Computes a dependency health score (0–100) and risk level for a
repository based on its parsed dependency data.

Scoring criteria and weights:
    - Version Pinning Quality   (40 pts)
    - Version Range Tightness   (20 pts)
    - Dependency Count Risk     (15 pts)
    - Outdated Dependency Flags (15 pts)
    - Manifest Completeness     (10 pts)
"""

import logging
import re
from datetime import datetime, timezone
from .parsers.base import Dependency

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk level thresholds
# ---------------------------------------------------------------------------
RISK_LOW = "LOW"          # score 80–100
RISK_MEDIUM = "MEDIUM"    # score 50–79
RISK_HIGH = "HIGH"        # score 0–49


def compute_risk_level(score: int) -> str:
    if score >= 80:
        return RISK_LOW
    elif score >= 50:
        return RISK_MEDIUM
    return RISK_HIGH


class HealthScorer:
    """
    Calculate a health score for a set of dependencies.
    """

    # Weight allocation (must sum to 100)
    W_PINNING = 40
    W_TIGHTNESS = 20
    W_COUNT = 15
    W_OUTDATED = 15
    W_COMPLETENESS = 10

    # Pinning scores per type (0.0–1.0)
    PINNING_SCORES = {
        "exact": 1.0,
        "compatible": 0.8,
        "range": 0.6,
        "minimum": 0.3,
        "complex": 0.4,
        "unpinned": 0.0,
    }

    def score(
        self,
        dependencies: list[Dependency],
        has_lock_file: bool = False,
        ecosystems_detected: int = 1,
        dependabot_alerts: list[dict] = None,
        repo_license: str = "N/A",
        pushed_at: str = None,
        stargazers_count: int = 0,
        is_archived: bool = False,
    ) -> dict:
        """
        Compute a health score and detailed breakdown.
        """
        dependabot_alerts = dependabot_alerts or []
        
        if not dependencies:
            return {
                "score": 0,
                "risk_level": RISK_HIGH,
                "breakdown": {
                    "pinning_quality": 0,
                    "range_tightness": 0,
                    "count_risk": 0,
                    "outdated_flags": 0,
                    "completeness": 0,
                },
                "summary_stats": {
                    "total_dependencies": 0,
                    "production_deps": 0,
                    "dev_deps": 0,
                    "pinned_count": 0,
                    "unpinned_count": 0,
                    "pinning_ratio": 0.0,
                },
            }

        total = len(dependencies)
        prod_deps = [d for d in dependencies if not d.is_dev]
        dev_deps = [d for d in dependencies if d.is_dev]

        # Weighting: Prod deps get 2.0x weight, dev deps get 1.0x
        def get_weight(d): return 2.0 if not d.is_dev else 1.0
        total_weight = sum(get_weight(d) for d in dependencies)

        # 1. Pinning Quality (0–40)
        def get_pinning_score(d):
            score = self.PINNING_SCORES.get(d.pinning_type, 0.0)
            if not has_lock_file and d.pinning_type in ("compatible", "minimum", "range"):
                # Context-Aware: these are perfect pinning strategies for published libraries
                return 1.0
            return score

        pinning_weighted = sum(
            get_pinning_score(d) * get_weight(d) for d in dependencies
        )
        pinning_ratio = pinning_weighted / total_weight if total_weight else 0
        pinning_score = round(pinning_ratio * self.W_PINNING, 1)

        pinned = sum(1 for d in dependencies if d.pinning_type != "unpinned")
        unpinned = total - pinned

        # 2. Range Tightness (0–20)
        tight_types = {"exact", "compatible"}
        if not has_lock_file:
            tight_types.update({"minimum", "range"})
        tight_weighted = sum(
            1.0 * get_weight(d) for d in dependencies if d.pinning_type in tight_types
        )
        tightness_ratio = tight_weighted / total_weight if total_weight else 0
        tightness_score = round(tightness_ratio * self.W_TIGHTNESS, 1)

        # 3. Dependency Count Risk (0–15)
        count_score = self._score_count(total, stargazers_count)

        # 4. Outdated / Risky Version Flags (0–15)
        outdated_score = self._score_outdated(dependencies)

        # 5. Manifest Completeness (0–10)
        completeness_score = self._score_completeness(has_lock_file, ecosystems_detected)

        # Base total score
        base_score = pinning_score + tightness_score + count_score + outdated_score + completeness_score

        # --- MAINTENANCE & PENALTIES ---
        maintenance_bonus = 0
        maintenance_penalty = 0
        days_since_push = 0
        if pushed_at:
            try:
                if pushed_at.endswith("Z"):
                    pushed_at = pushed_at[:-1] + "+00:00"
                last_push = datetime.fromisoformat(pushed_at)
                now = datetime.now(timezone.utc)
                days_since_push = (now - last_push).days
                if days_since_push <= 30:
                    maintenance_bonus = 10
                elif days_since_push > 1095:  # > 3 years
                    maintenance_penalty = 30
                elif days_since_push > 730:   # > 2 years
                    maintenance_penalty = 30
                elif days_since_push > 365:   # > 1 year
                    maintenance_penalty = 15
                elif days_since_push > 180:   # > 6 months
                    maintenance_penalty = 5
            except Exception as e:
                logger.debug(f"Failed to parse pushed_at: {e}")

        if is_archived:
            maintenance_penalty = max(maintenance_penalty, 30)

        # Apply maintenance bonus
        base_score = min(100.0, base_score + maintenance_bonus)
        
        # 1. Vulnerability Penalty (Dependabot)
        cve_penalty = 0
        for alert in dependabot_alerts:
            sev = alert.get("security_vulnerability", {}).get("severity", "").lower()
            if sev == "critical":
                cve_penalty += 15
            elif sev == "high":
                cve_penalty += 8
            elif sev == "medium":
                cve_penalty += 3
            elif sev == "low":
                cve_penalty += 1

        # 2. License Risk Penalty
        # If repo is proprietary (NOASSERTION/N/A) and uses GPL dependencies
        license_penalty = 0
        if repo_license in ["NOASSERTION", "N/A"]:
            for d in dependencies:
                # Basic check for GPL (excluding LGPL)
                if not d.license:
                    continue
                lic = d.license.lower()
                if "gpl" in lic and "lgpl" not in lic:
                    license_penalty += 10
                    break # Apply once

        total_score = min(100, max(0, round(base_score - cve_penalty - license_penalty - maintenance_penalty)))
        
        # Override risk level if officially dead
        final_risk = compute_risk_level(total_score)
        if is_archived or days_since_push > 730:
            final_risk = RISK_HIGH

        return {
            "score": total_score,
            "risk_level": final_risk,
            "breakdown": {
                "base_score": round(base_score, 1),
                "is_archived": is_archived,
                "pinning_quality": pinning_score,
                "range_tightness": tightness_score,
                "count_risk": count_score,
                "outdated_flags": outdated_score,
                "completeness": completeness_score,
                "cve_penalty": cve_penalty,
                "license_penalty": license_penalty,
                "maintenance_penalty": maintenance_penalty,
            },
            "summary_stats": {
                "total_dependencies": total,
                "production_deps": len(prod_deps),
                "dev_deps": len(dev_deps),
                "pinned_count": pinned,
                "unpinned_count": unpinned,
                "pinning_ratio": round(pinning_ratio, 3),
            },
        }

    # ------------------------------------------------------------------
    # Sub-scoring functions
    # ------------------------------------------------------------------
    def _score_count(self, total: int, stars: int) -> float:
        """Fewer dependencies = healthier, UNLESS highly trusted repo."""
        if stars > 10000:
            return self.W_COUNT  # Full score, massive community trust offsets risk
        
        # Reduce penalty for >1k stars
        penalty_reduction = 0.5 if stars > 1000 else 1.0

        if total <= 10:
            return self.W_COUNT
        elif total <= 30:
            return self.W_COUNT - ((self.W_COUNT * 0.1) * penalty_reduction)
        elif total <= 60:
            return self.W_COUNT - ((self.W_COUNT * 0.3) * penalty_reduction)
        elif total <= 100:
            return self.W_COUNT - ((self.W_COUNT * 0.5) * penalty_reduction)
        else:
            return self.W_COUNT - ((self.W_COUNT * 0.7) * penalty_reduction)

    def _score_outdated(self, deps: list[Dependency]) -> float:
        """Penalize pre-1.0 versions and packages significantly behind latest."""
        if not deps:
            return self.W_OUTDATED
            
        penalty = 0.0
        for d in deps:
            v = d.version_constraint
            if not v:
                continue
            
            # Pre-1.0 (0.x.y)
            if re.match(r"[=~^<>]*0\.", v):
                penalty += 0.5
                continue
                
            # Major version behind latest
            if d.latest_version:
                try:
                    # Very naive extraction of first digits
                    curr_match = re.search(r"(\d+)", v)
                    latest_match = re.search(r"(\d+)", d.latest_version)
                    if curr_match and latest_match:
                        curr_maj = int(curr_match.group(1))
                        lat_maj = int(latest_match.group(1))
                        if lat_maj > curr_maj:
                            diff = lat_maj - curr_maj
                            # Penalize more for each major version behind
                            penalty += min(2.0, diff * 0.5)
                except Exception:
                    pass
                    
        score = max(0.0, self.W_OUTDATED - penalty)
        return round(score, 1)

    def _score_completeness(self, has_lock: bool, ecosystems: int) -> float:
        """Lock file presence adds confidence."""
        base = self.W_COMPLETENESS * 0.5
        if has_lock:
            base += self.W_COMPLETENESS * 0.5
        return round(base, 1)
