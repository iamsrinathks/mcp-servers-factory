import os
import base64
import datetime
import requests
from typing import List, Dict, Optional
import logging as logger
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from contextlib import asynccontextmanager
import contextlib

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------
logger.basicConfig(level=logger.INFO)

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # fallback global PAT
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8080/callback")

BASE_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
user_tokens: Dict[str, str] = {}  # per-user OAuth tokens (demo only)

# --------------------------------------------------------------------
# Auth Helpers
# --------------------------------------------------------------------
def get_auth_header(user: Optional[str] = None) -> Dict[str, str]:
    token = None
    if user and user in user_tokens:
        token = user_tokens[user]
    elif GITHUB_TOKEN:
        token = GITHUB_TOKEN
    if not token:
        raise HTTPException(status_code=401, detail="No GitHub token available")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}

def github_request(method: str, url: str, user: Optional[str] = None, **kwargs):
    resp = requests.request(method, url, headers=get_auth_header(user), **kwargs)
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json() if resp.text else {}

def check_readonly(request: Request):
    if request.headers.get("X-MCP-Readonly", "").lower() == "true":
        raise HTTPException(status_code=403, detail="Readonly mode enforced")

# --------------------------------------------------------------------
# OAuth Routes
# --------------------------------------------------------------------
oauth_app = FastAPI()

@oauth_app.get("/login")
def login():
    return RedirectResponse(
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=repo read:user"
    )

@oauth_app.get("/callback")
def callback(code: str):
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
    user_tokens["demo_user"] = token
    return JSONResponse({"access_token": token})

@oauth_app.get("/me")
def me():
    return github_request("GET", f"{BASE_URL}/user", user="demo_user")

# --------------------------------------------------------------------
# MCP Server
# --------------------------------------------------------------------
mcp = FastMCP("GitHubMCPServer", stateless_http=True, json_response=True)

# === Your existing tools (adapted to support per-user) ===

@mcp.tool()
def create_branch(owner: str, repo: str, new_branch: str, base_branch: str = "main", user: str = None, request: Request = None) -> Dict:
    check_readonly(request)
    ref_url = f"{BASE_URL}/repos/{owner}/{repo}/git/ref/heads/{base_branch}"
    ref_data = github_request("GET", ref_url, user)
    sha = ref_data["object"]["sha"]
    create_url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs"
    resp = github_request("POST", create_url, user, json={"ref": f"refs/heads/{new_branch}", "sha": sha})
    return {"message": f"Branch '{new_branch}' created", "ref": resp}

@mcp.tool()
def create_or_update_file(owner: str, repo: str, branch: str, path: str, content: str, message: str, sha: Optional[str] = None, user: str = None, request: Request = None) -> Dict:
    check_readonly(request)
    encoded = base64.b64encode(content.encode()).decode()
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
    payload = {"message": message, "content": encoded, "branch": branch}
    if sha: payload["sha"] = sha
    resp = github_request("PUT", url, user, json=payload)
    return {"message": f"File '{path}' updated", "commit": resp.get("commit")}

@mcp.tool()
def get_contents(owner: str, repo: str, path: str, ref: str = "main", user: str = None) -> Dict:
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    return github_request("GET", url, user)

@mcp.tool()
def create_pull_request(owner: str, repo: str, title: str, head: str, base: str = "main", body: str = "", user: str = None, request: Request = None) -> Dict:
    check_readonly(request)
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
    resp = github_request("POST", url, user, json={"title": title, "head": head, "base": base, "body": body})
    return {"message": "PR created", "url": resp.get("html_url"), "number": resp.get("number")}

@mcp.tool()
def merge_pull_request(owner: str, repo: str, pr_number: int, commit_message: str = "Merging via MCP", user: str = None, request: Request = None) -> Dict:
    check_readonly(request)
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    resp = github_request("PUT", url, user, json={"commit_message": commit_message})
    return {"message": f"PR #{pr_number} merged", "sha": resp.get("sha")}

# === Conversational tools (new) ===

@mcp.tool()
def query_github(query: str, user: str = "demo_user") -> Dict:
    if "pull" in query.lower():
        return github_request("GET", "/repos/org/repo/pulls?state=open", user)
    if "issue" in query.lower():
        return github_request("GET", "/repos/org/repo/issues?state=open", user)
    if "release" in query.lower():
        return github_request("GET", "/repos/org/repo/releases", user)
    return {"message": f"Query not understood: {query}"}

@mcp.tool()
def weekly_digest(user: str = "demo_user") -> Dict:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat() + "Z"
    prs = github_request("GET", f"/repos/org/repo/pulls?state=all&sort=updated&direction=desc", user)
    issues = github_request("GET", f"/repos/org/repo/issues?since={since}", user)
    md = "# Weekly Digest\n\n"
    md += "## Pull Requests\n"
    md += "\n".join([f"- {pr['title']} (#{pr['number']})" for pr in prs]) or "No PRs\n"
    md += "\n\n## Issues\n"
    md += "\n".join([f"- {issue['title']} (#{issue['number']})" for issue in issues]) or "No issues\n"
    return {"digest": md}

# --------------------------------------------------------------------
# FastAPI + Lifespan
# --------------------------------------------------------------------
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield

main_app = FastAPI(lifespan=lifespan)

@main_app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Mount both
main_app.mount("/mcp", mcp_app)
main_app.mount("/", oauth_app)
