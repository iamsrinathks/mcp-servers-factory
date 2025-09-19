import os
import base64
import json
from typing import List, Dict, Optional, Union
import logging as logger

import requests
from requests.auth import HTTPBasicAuth

from fastapi import FastAPI
from contextlib import asynccontextmanager
import contextlib

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------------
logger.basicConfig(level=logger.INFO)

# --------------------------------------------------------------------
# Confluence client configuration (with PAT)
# --------------------------------------------------------------------
CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
CONFLUENCE_PAT = os.getenv("CONFLUENCE_PAT")  # Personal Access Token

if not CONFLUENCE_BASE_URL:
    raise RuntimeError("Please set CONFLUENCE_BASE_URL (e.g., https://your-domain.atlassian.net/wiki)")
if not CONFLUENCE_PAT:
    raise RuntimeError("Please set CONFLUENCE_PAT (Confluence Personal Access Token)")

API_BASE = f"{CONFLUENCE_BASE_URL.rstrip('/')}/rest/api"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CONFLUENCE_PAT}",
}

def cf_request(method: str, url: str, **kwargs) -> Dict:
    resp = requests.request(method, url, headers=HEADERS, **kwargs)
    if not resp.ok:
        # Try to provide meaningful error context
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Confluence API error {resp.status_code}: {detail}")
    if resp.text:
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}
    return {}


# Helper to format Confluence storage body
def storage_body_html(html: str) -> Dict:
    # Confluence Cloud expects 'storage' representation for rich content
    return {
        "storage": {
            "value": html,
            "representation": "storage"
        }
    }

# --------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------
mcp = FastMCP("ConfluenceMCPServer", stateless_http=True, json_response=True)

# --------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------
@mcp.tool()
def confluence_create_page(space_key: str, title: str, html_content: str, parent_page_id: Optional[str] = None) -> Dict:
    """
    Create a new Confluence page in the given space. Optionally set a parent page (to create a child page).
    """
    data = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": storage_body_html(html_content)
    }
    if parent_page_id:
        data["ancestors"] = [{"id": str(parent_page_id)}]
    url = f"{API_BASE}/content"
    created = cf_request("POST", url, json=data)
    return {
        "message": "Page created",
        "id": created.get("id"),
        "title": created.get("title"),
        "link": created.get("_links", {}).get("base", "") + created.get("_links", {}).get("webui", "")
    }

@mcp.tool()
def confluence_get_page(page_id: Optional[str] = None, title: Optional[str] = None, space_key: Optional[str] = None, expand_body: bool = True) -> Dict:
    """
    Get a page by ID, or by title + space_key. If expand_body is True, returns storage HTML content.
    """
    expand = "body.storage,version" if expand_body else "version"
    if page_id:
        url = f"{API_BASE}/content/{page_id}?expand={expand}"
        return cf_request("GET", url)
    if not title or not space_key:
        raise RuntimeError("Provide either page_id OR (title and space_key).")
    url = f"{API_BASE}/content?title={requests.utils.quote(title)}&spaceKey={requests.utils.quote(space_key)}&expand={expand}"
    result = cf_request("GET", url)
    results = result.get("results", [])
    if not results:
        return {"message": "No page found", "results": []}
    return results[0]

@mcp.tool()
def confluence_update_page(page_id: str, new_title: Optional[str] = None, new_html_content: Optional[str] = None, minor_edit: bool = False) -> Dict:
    """
    Update an existing page's title and/or storage body. Automatically increments version.
    """
    # Get current version
    current = cf_request("GET", f"{API_BASE}/content/{page_id}?expand=version")
    version = current.get("version", {}).get("number")
    if not version:
        raise RuntimeError("Could not determine current version number for the page.")
    payload: Dict[str, Union[str, Dict]] = {
        "id": str(page_id),
        "type": "page",
        "title": new_title or current.get("title"),
        "version": {"number": version + 1, "minorEdit": minor_edit}
    }
    if new_html_content is not None:
        payload["body"] = storage_body_html(new_html_content)
    updated = cf_request("PUT", f"{API_BASE}/content/{page_id}", json=payload)
    return {
        "message": "Page updated",
        "id": updated.get("id"),
        "title": updated.get("title"),
        "version": updated.get("version", {}).get("number")
    }

@mcp.tool()
def confluence_delete_page(page_id: str, status: str = "current") -> Dict:
    """
    Delete a Confluence page. status usually 'current' (default). For trash/restore behavior refer to Confluence docs.
    """
    url = f"{API_BASE}/content/{page_id}?status={requests.utils.quote(status)}"
    cf_request("DELETE", url)
    return {"message": f"Page {page_id} deleted", "status": status}

@mcp.tool()
def confluence_add_comment(page_id: str, html_content: str) -> Dict:
    """
    Add a comment to a Confluence page (storage HTML).
    """
    url = f"{API_BASE}/content/{page_id}/child/comment"
    data = {
        "type": "comment",
        "container": {"id": str(page_id), "type": "page"},
        "body": storage_body_html(html_content)
    }
    created = cf_request("POST", url, json=data)
    return {"message": "Comment added", "id": created.get("id")}

@mcp.tool()
def confluence_get_comments(page_id: str, limit: int = 50, start: int = 0) -> Dict:
    """
    Get comments for a page. Returns storage HTML for each comment.
    """
    url = f"{API_BASE}/content/{page_id}/child/comment?expand=body.storage,version&limit={limit}&start={start}"
    return cf_request("GET", url)

@mcp.tool()
def confluence_add_label(page_id: str, labels: List[str]) -> Dict:
    """
    Add one or more labels to a page. Labels will be added with 'global' prefix.
    """
    url = f"{API_BASE}/content/{page_id}/label"
    payload = [{"prefix": "global", "name": name} for name in labels]
    resp = cf_request("POST", url, json=payload)
    return {"message": f"Added {len(labels)} label(s)", "labels": resp}

@mcp.tool()
def confluence_get_labels(page_id: str, limit: int = 200, start: int = 0) -> Dict:
    """
    Get labels on a page.
    """
    url = f"{API_BASE}/content/{page_id}/label?limit={limit}&start={start}"
    return cf_request("GET", url)

@mcp.tool()
def confluence_get_page_children(page_id: str, limit: int = 50, start: int = 0, expand_body: bool = False) -> Dict:
    """
    Get child pages of a page. Optionally expand storage body.
    """
    expand = "body.storage,version" if expand_body else "version"
    url = f"{API_BASE}/content/{page_id}/child/page?expand={expand}&limit={limit}&start={start}"
    return cf_request("GET", url)

@mcp.tool()
def confluence_search(query: Optional[str] = None, cql: Optional[str] = None, limit: int = 25, start: int = 0, expand_body: bool = False) -> Dict:
    """
    Search Confluence. Use either 'query' (simple) or 'cql' (advanced). If expand_body, includes storage content where applicable.
    """
    if cql:
        expand = "body.storage" if expand_body else None
        url = f"{API_BASE}/search?cql={requests.utils.quote(cql)}&limit={limit}&start={start}"
        if expand:
            url += f"&expand={expand}"
        return cf_request("GET", url)

    if query:
        # Simple search: use CQL under the hood (title, text)
        safe = requests.utils.quote(query)
        cql_expr = f'text ~ "{safe}" OR title ~ "{safe}"'
        url = f"{API_BASE}/search?cql={cql_expr}&limit={limit}&start={start}"
        if expand_body:
            url += "&expand=body.storage"
        return cf_request("GET", url)

    raise RuntimeError("Provide either 'query' or 'cql' for search.")

# --------------------------------------------------------------------
# FastAPI wrapper with lifespan to start MCP
# --------------------------------------------------------------------
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        # Start MCP session manager so task group is initialized
        await stack.enter_async_context(mcp.session_manager.run())
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Mount MCP server at "/"
app.mount("/", mcp_app)
