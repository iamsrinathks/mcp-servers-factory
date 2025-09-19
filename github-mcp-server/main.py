import os
import base64
import requests
from typing import List, Dict, Optional
import logging as logger
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
import contextlib

from mcp.server.fastmcp import FastMCP


# --------------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------------
logger.basicConfig(level=logger.INFO)

# --------------------------------------------------------------------
# GitHub client helper
# --------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise RuntimeError("Please set GITHUB_TOKEN in the environment (with repo scope)")

BASE_URL = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def github_request(method: str, url: str, **kwargs):
    resp = requests.request(method, url, headers=HEADERS, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"GitHub API error {resp.status_code}: {resp.text}")
    return resp.json() if resp.text else {}


def check_readonly(request: Request):
    """Raise if MCP client requested readonly mode."""
    if request.headers.get("X-MCP-Readonly", "").lower() == "true":
        raise HTTPException(status_code=403, detail="Readonly mode enforced")

# --------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------
mcp = FastMCP("GitHubMCPServer", stateless_http=True, json_response=True)

# --------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------
@mcp.tool()
def create_branch(owner: str, repo: str, new_branch: str, base_branch: str = "main", request: Request = None) -> Dict:
    check_readonly(request)
    ref_url = f"{BASE_URL}/repos/{owner}/{repo}/git/ref/heads/{base_branch}"
    ref_data = github_request("GET", ref_url)
    sha = ref_data["object"]["sha"]

    create_url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs"
    resp = github_request("POST", create_url, json={"ref": f"refs/heads/{new_branch}", "sha": sha})
    return {"message": f"Branch '{new_branch}' created", "ref": resp}

@mcp.tool()
def create_or_update_file(owner: str, repo: str, branch: str, path: str, content: str, message: str, sha: Optional[str] = None, request: Request = None) -> Dict:
    check_readonly(request)
    encoded = base64.b64encode(content.encode()).decode()
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
    payload = {"message": message, "content": encoded, "branch": branch}
    if sha:
        payload["sha"] = sha
    resp = github_request("PUT", url, json=payload)
    return {"message": f"File '{path}' updated", "commit": resp.get("commit")}

@mcp.tool()
def get_contents(owner: str, repo: str, path: str, ref: str = "main") -> Dict:
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    return github_request("GET", url)

@mcp.tool()
def create_pull_request(owner: str, repo: str, title: str, head: str, base: str = "main", body: str = "", request: Request = None) -> Dict:
    check_readonly(request)
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
    resp = github_request("POST", url, json={"title": title, "head": head, "base": base, "body": body})
    return {"message": "PR created", "url": resp.get("html_url"), "number": resp.get("number")}

@mcp.tool()
def merge_pull_request(owner: str, repo: str, pr_number: int, commit_message: str = "Merging via MCP", request: Request = None) -> Dict:
    check_readonly(request)
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    resp = github_request("PUT", url, json={"commit_message": commit_message})
    return {"message": f"PR #{pr_number} merged", "sha": resp.get("sha")}

@mcp.tool()
def push_multiple_files(owner: str, repo: str, branch: str, files: List[Dict[str, str]], message: str, request: Request = None) -> Dict:
    check_readonly(request)
    ref_url = f"{BASE_URL}/repos/{owner}/{repo}/git/ref/heads/{branch}"
    ref_data = github_request("GET", ref_url)
    latest_commit_sha = ref_data["object"]["sha"]

    commit_url = f"{BASE_URL}/repos/{owner}/{repo}/git/commits/{latest_commit_sha}"
    commit_data = github_request("GET", commit_url)
    base_tree = commit_data["tree"]["sha"]

    tree_entries = []
    for f in files:
        blob = github_request("POST", f"{BASE_URL}/repos/{owner}/{repo}/git/blobs", json={"content": f["content"], "encoding": "utf-8"})
        tree_entries.append({"path": f["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]})

    tree = github_request("POST", f"{BASE_URL}/repos/{owner}/{repo}/git/trees", json={"base_tree": base_tree, "tree": tree_entries})

    commit = github_request("POST", f"{BASE_URL}/repos/{owner}/{repo}/git/commits",
                            json={"message": message, "tree": tree["sha"], "parents": [latest_commit_sha]})

    github_request("PATCH", ref_url, json={"sha": commit["sha"]})
    return {"message": f"Committed {len(files)} files", "commit": commit}

@mcp.tool()
def update_pr_branch(owner: str, repo: str, pr_number: int, request: Request = None) -> Dict:
    check_readonly(request)
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/update-branch"
    resp = github_request("PUT", url, json={})
    return {"message": f"PR #{pr_number} branch update requested", "response": resp}

# --------------------------------------------------------------------
# FastAPI wrapper with lifespan to start MCP
# --------------------------------------------------------------------
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        # ✅ Start MCP session manager so task group is initialized
        await stack.enter_async_context(mcp.session_manager.run())
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Mount MCP server at "/"
app.mount("/", mcp_app)
