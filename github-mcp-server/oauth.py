import os
import base64
import datetime
import requests
from typing import Dict, Optional, List
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from contextlib import asynccontextmanager
import contextlib
import logging as logger

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------------
logger.basicConfig(level=logger.INFO)

# --------------------------------------------------------------------
# OAuth & Config
# --------------------------------------------------------------------
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8080/callback")

# Replace with GitHub Enterprise API base if needed
BASE_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

user_tokens: Dict[str, str] = {}  # in-memory token store

# --------------------------------------------------------------------
# FastAPI App
# --------------------------------------------------------------------
app = FastAPI()

@app.get("/login")
def login():
    """Redirect user to GitHub OAuth login."""
    return RedirectResponse(
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=repo read:user"
    )

@app.get("/callback")
def callback(code: str):
    """OAuth callback exchange code for token."""
    resp = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    token = resp.json().get("access_token")
    if not token:
        raise HTTPException(status_code=400, detail="OAuth failed")
    # For demo store under static user
    user_tokens["demo_user"] = token
    return JSONResponse({"access_token": token})

@app.get("/me")
def me():
    """Fetch current user profile with stored token."""
    token = user_tokens.get("demo_user")
    if not token:
        raise HTTPException(status_code=401, detail="Login first")
    resp = requests.get(
        f"{BASE_URL}/user",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()

# --------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------
mcp = FastMCP("GitHubMCPServer", stateless_http=True, json_response=True)

def github_request(user: str, method: str, path: str, **kwargs):
    token = user_tokens.get(user)
    if not token:
        raise HTTPException(status_code=401, detail="User not authenticated")
    url = f"{BASE_URL}{path}"
    resp = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
        **kwargs
    )
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()

@mcp.tool()
def query_github(query: str, user: str = "demo_user") -> Dict:
    """
    Natural language GitHub query → structured API call.
    Example: "open pull requests", "issues with bug label"
    """
    if "pull" in query.lower():
        return github_request(user, "GET", "/repos/org/repo/pulls?state=open")
    if "issue" in query.lower():
        return github_request(user, "GET", "/repos/org/repo/issues?state=open")
    if "release" in query.lower():
        return github_request(user, "GET", "/repos/org/repo/releases")
    return {"message": f"Query not understood: {query}"}

@mcp.tool()
def weekly_digest(user: str = "demo_user") -> Dict:
    """
    Summarize last week's GitHub activity.
    """
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat() + "Z"

    prs = github_request(user, "GET", f"/repos/org/repo/pulls?state=all&sort=updated&direction=desc")
    issues = github_request(user, "GET", f"/repos/org/repo/issues?since={since}")

    md = "# Weekly Digest\n\n"
    md += "## Pull Requests\n"
    md += "\n".join([f"- {pr['title']} (#{pr['number']})" for pr in prs]) or "No PRs\n"
    md += "\n\n## Issues\n"
    md += "\n".join([f"- {issue['title']} (#{issue['number']})" for issue in issues]) or "No issues\n"

    return {"digest": md}

# --------------------------------------------------------------------
# FastAPI wrapper with lifespan for MCP
# --------------------------------------------------------------------
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield

mcp_api = FastAPI(lifespan=lifespan)

@mcp_api.get("/healthz")
def healthz():
    return {"status": "ok"}

# Mount MCP under /
mcp_api.mount("/", mcp_app)
app.mount("/mcp", mcp_api)
