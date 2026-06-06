"""
Web Dashboard Server
====================
FastAPI backend that exposes the existing dependency analyzer engine
as a REST API, and serves the dashboard HTML frontend.

Usage::

    python server.py
    # Then open http://localhost:8000 in your browser

    # With a custom port:
    python server.py --port 9000
"""

import argparse
import json
import os
import sys
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Ensure the analyzer package is importable
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from analyzer import __version__
from analyzer.github_client import GitHubClient, GitHubAPIError, RateLimitError
from analyzer.cli import analyze_repo

# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------
DATA_DIR = PROJECT_DIR / "data"
DATA_FILE = DATA_DIR / "analysis_results.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("[]", encoding="utf-8")


def _load_history() -> list[dict]:
    _ensure_data_dir()
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_record(record: dict):
    history = _load_history()
    history.append(record)
    DATA_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _clear_history():
    _ensure_data_dir()
    DATA_FILE.write_text("[]", encoding="utf-8")


def _format_result_for_storage(analysis: dict) -> dict:
    """
    Reshape the analyzer output into the storage schema designed for
    future MongoDB / Supabase migration (zero restructuring needed).
    """
    health = analysis.get("health", {})
    return {
        "id": str(uuid.uuid4()),
        "repo": analysis.get("repository", ""),
        "analyzed_at": analysis.get("analyzed_at", datetime.now(timezone.utc).isoformat()),
        "metadata": analysis.get("repo_info", {}),
        "ecosystems": analysis.get("ecosystems", {}),
        "dependencies": analysis.get("dependencies", []),
        "dependabot_alerts": analysis.get("dependabot_alerts", []),
        "health_score": health.get("score", 0),
        "risk_level": health.get("risk_level", "HIGH"),
        "score_breakdown": health.get("breakdown", {}),
        "summary_stats": health.get("summary_stats", {}),
        "errors": analysis.get("errors", []),
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Dependency Analyzer Dashboard",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the single-file HTML dashboard."""
    html_path = PROJECT_DIR / "dashboard.html"
    if not html_path.exists():
        return HTMLResponse("<h1>dashboard.html not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/rate-limit")
async def get_rate_limit():
    """Return current GitHub API rate limit info."""
    try:
        client = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
        rl = client.get_rate_limit()
        return JSONResponse(rl)
    except GitHubAPIError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/analyze")
async def analyze_single(request: Request):
    """
    Analyze a single repository.

    Body: {"repo": "owner/repo"}
    """
    body = await request.json()
    repo = body.get("repo", "").strip()
    token = os.environ.get("GITHUB_TOKEN")

    if not repo or "/" not in repo or len(repo.split("/")) != 2:
        return JSONResponse(
            {"error": f"Invalid repo format: '{repo}'. Use owner/repo"},
            status_code=400,
        )

    try:
        client = GitHubClient(token=token)
        analysis = analyze_repo(client, repo)
        record = _format_result_for_storage(analysis)
        _save_record(record)
        return JSONResponse(record)
    except RateLimitError as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    except GitHubAPIError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/analyze/batch")
async def analyze_batch(request: Request):
    """
    Analyze multiple repositories using Server-Sent Events for progress.

    Body: {"repos": ["owner/repo1", "owner/repo2", ...]}

    Returns SSE stream with events:
        - progress: {"index": 1, "total": 5, "repo": "owner/repo", "status": "analyzing"}
        - result: full analysis record
        - error: {"repo": "owner/repo", "error": "message"}
        - done: {"completed": 5, "total": 5}
    """
    body = await request.json()
    repos = body.get("repos", [])
    token = os.environ.get("GITHUB_TOKEN")

    # Validate
    valid_repos = []
    for r in repos:
        r = r.strip()
        if r and "/" in r and len(r.split("/")) == 2:
            valid_repos.append(r)

    if not valid_repos:
        return JSONResponse({"error": "No valid repositories provided"}, status_code=400)

    def event_stream():
        client = GitHubClient(token=token)
        completed = 0

        for i, repo in enumerate(valid_repos):
            # Send progress event
            progress_data = json.dumps({
                "index": i + 1,
                "total": len(valid_repos),
                "repo": repo,
                "status": "analyzing",
            })
            yield f"event: progress\ndata: {progress_data}\n\n"

            try:
                analysis = analyze_repo(client, repo)
                record = _format_result_for_storage(analysis)
                _save_record(record)
                completed += 1
                yield f"event: result\ndata: {json.dumps(record, default=str)}\n\n"
            except RateLimitError as exc:
                error_data = json.dumps({"repo": repo, "error": str(exc), "fatal": True})
                yield f"event: error\ndata: {error_data}\n\n"
                break  # Stop batch on rate limit
            except Exception as exc:
                error_data = json.dumps({"repo": repo, "error": str(exc), "fatal": False})
                yield f"event: error\ndata: {error_data}\n\n"

        done_data = json.dumps({"completed": completed, "total": len(valid_repos)})
        yield f"event: done\ndata: {done_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history():
    """Return all saved analysis results."""
    return JSONResponse(_load_history())


@app.get("/api/history/{record_id}")
async def get_history_record(record_id: str):
    """Return a single saved result by UUID."""
    history = _load_history()
    for record in history:
        if record.get("id") == record_id:
            return JSONResponse(record)
    return JSONResponse({"error": "Record not found"}, status_code=404)


@app.delete("/api/history")
async def clear_history():
    """Clear all saved analysis history."""
    _clear_history()
    return JSONResponse({"message": "History cleared"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _ensure_data_dir()

    parser = argparse.ArgumentParser(description="Dependency Analyzer Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Dependency Analyzer Dashboard v{__version__}")
    print(f"  Open http://{args.host}:{args.port} in your browser")
    print(f"{'='*60}\n")

    uvicorn.run(app, host=args.host, port=args.port)
