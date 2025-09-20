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
# GitLab client helper
# --------------------------------------------------------------------
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
if not GITLAB_TOKEN:
    raise RuntimeError("Please set GITLAB_TOKEN in the environment")

# Example: https://code.lioncloud.net/api/v4
GITLAB_BASE_URL = os.getenv("GITLAB_BASE_URL", "https://code.lioncloud.net/api/v4")

HEADERS = {
    "PRIVATE-TOKEN": GITLAB_TOKEN,
    "Accept": "application/json"
}

def gitlab_request(method: str, url: str, **kwargs):
    resp = requests.request(method, url, headers=HEADERS, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"GitLab API error {resp.status_code}: {resp.text}")
    return resp.json() if resp.text else {}

def check_readonly(request: Request):
    """Raise if MCP client requested readonly mode."""
    if request.headers.get("X-MCP-Readonly", "").lower() == "true":
        raise HTTPException(status_code=403, detail="Readonly mode enforced")

# --------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------
mcp = FastMCP("GitLabMCPServer", stateless_http=True, json_response=True)

# --------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------
@mcp.tool()
def create_branch(project_id: str, new_branch: str, base_branch: str = "main" ) -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/repository/branches"
    resp = gitlab_request("POST", url, json={"branch": new_branch, "ref": base_branch})
    return {"message": f"Branch '{new_branch}' created", "branch": resp}

@mcp.tool()
def create_or_update_file(project_id: str, branch: str, path: str, content: str, message: str, sha: Optional[str] = None ) -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/repository/files/{requests.utils.quote(path, safe='')}"
    payload = {
        "branch": branch,
        "content": content,
        "commit_message": message
    }
    method = "PUT" if sha else "POST"
    resp = gitlab_request(method, url, json=payload)
    return {"message": f"File '{path}' updated", "response": resp}

@mcp.tool()
def get_contents(project_id: str, path: str, ref: str = "main") -> Dict:
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/repository/files/{requests.utils.quote(path, safe='')}?ref={ref}"
    return gitlab_request("GET", url)

@mcp.tool()
def create_merge_request(project_id: str, title: str, source_branch: str, target_branch: str = "main", description: str = "") -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/merge_requests"
    payload = {
        "title": title,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "description": description
    }
    resp = gitlab_request("POST", url, json=payload)
    return {"message": "Merge request created", "url": resp.get("web_url"), "iid": resp.get("iid")}

@mcp.tool()
def merge_merge_request(project_id: str, mr_iid: int, merge_commit_message: str = "Merging via MCP" ) -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/merge_requests/{mr_iid}/merge"
    resp = gitlab_request("PUT", url, json={"merge_commit_message": merge_commit_message})
    return {"message": f"MR !{mr_iid} merged", "sha": resp.get("sha")}

@mcp.tool()
def push_multiple_files(project_id: str, branch: str, files: List[Dict[str, str]], message: str ) -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/repository/commits"
    actions = [
        {"action": "create", "file_path": f["path"], "content": f["content"]}
        for f in files
    ]
    payload = {"branch": branch, "commit_message": message, "actions": actions}
    resp = gitlab_request("POST", url, json=payload)
    return {"message": f"Committed {len(files)} files", "commit": resp}

@mcp.tool()
def update_mr_branch(project_id: str, mr_iid: int ) -> Dict:
    check_readonly(request)
    url = f"{GITLAB_BASE_URL}/projects/{project_id}/merge_requests/{mr_iid}/rebase"
    resp = gitlab_request("PUT", url)
    return {"message": f"MR !{mr_iid} rebase requested", "response": resp}

# --------------------------------------------------------------------
# FastAPI wrapper with lifespan to start MCP
# --------------------------------------------------------------------
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Mount MCP server at "/"
app.mount("/", mcp_app)
