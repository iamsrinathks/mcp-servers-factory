import os
import json
import datetime
from typing import List, Optional, Any, Dict

# Third-party imports
try:
    from google.cloud import logging as gcp_logging
    from google.cloud.logging import DESCENDING
    from google.api_core import exceptions as google_exceptions
except ImportError:
    raise ImportError(
        "Google Cloud Logging library not found. "
        "Please install it using: pip install google-cloud-logging"
    )

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "MCP SDK not found. Please install it using: pip install mcp[cli]"
    )

# Initialize the MCP Server
# We use FastMCP for a production-quality, type-safe implementation.
mcp = FastMCP("gcp-logging-server")

# --- Security & Helper Functions ---

def _get_gcp_client(project_id: Optional[str] = None) -> gcp_logging.Client:
    """
    Securely creates a Google Cloud Logging client.
    
    Security Note:
    - Relies on Application Default Credentials (ADC).
    - Ensure the environment variable GOOGLE_APPLICATION_CREDENTIALS is set 
      or the user has run `gcloud auth application-default login`.
    """
    try:
        return gcp_logging.Client(project=project_id)
    except google_exceptions.GoogleAPICallError as e:
        raise RuntimeError(f"Failed to initialize GCP Client: {str(e)}")

def _json_serializer(obj: Any) -> Any:
    """Helper to serialize datetime objects for JSON output."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)

def _format_entry(entry: Any) -> Dict[str, Any]:
    """
    Sanitizes and formats a GCP LogEntry for the AI context.
    """
    payload = entry.payload
    if isinstance(payload, dict):
        payload_data = payload
    else:
        payload_data = {"message": str(payload)}

    return {
        "insert_id": entry.insert_id,
        "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        "severity": entry.severity,
        "resource_type": entry.resource.type if entry.resource else "unknown",
        "log_name": entry.log_name,
        "payload": payload_data,
        "labels": entry.labels,
    }

# --- Tool Definitions ---

@mcp.tool()
def search_logs(
    filter_expression: str,
    project_id: Optional[str] = None,
    limit: int = 10,
    order_by: str = "timestamp desc"
) -> str:
    """
    Search and filter logs from Google Cloud Platform.
    
    Args:
        filter_expression: The GCP advanced logs filter string. 
                           Examples:
                           - 'severity>=ERROR'
                           - 'resource.type="gce_instance" AND jsonPayload.message:"connection error"'
        project_id: The GCP Project ID. If not provided, uses the default from credentials.
        limit: Maximum number of log entries to return (default 10, max 50).
        order_by: Sort order (default "timestamp desc").
        
    Returns:
        A JSON string containing a list of log entries.
    """
    # Security: Input Validation
    max_limit = 50
    if limit > max_limit:
        return f"Error: Limit exceeds maximum allowed ({max_limit}). Please request fewer logs."

    try:
        client = _get_gcp_client(project_id)
        
        entries_iterator = client.list_entries(
            filter_=filter_expression,
            order_by=order_by,
            max_results=limit
        )

        results = []
        for entry in entries_iterator:
            results.append(_format_entry(entry))

        if not results:
            return "No logs found matching the criteria."

        return json.dumps(results, default=_json_serializer, indent=2)

    except google_exceptions.InvalidArgument as e:
        return f"Invalid Filter Expression: {str(e)}"
    except google_exceptions.NotFound as e:
        return f"Project not found: {str(e)}"
    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"

@mcp.tool()
def list_log_names(project_id: Optional[str] = None) -> str:
    """
    Lists the names of logs available in the project (e.g., 'syslog', 'cloudaudit').
    
    Args:
        project_id: The GCP Project ID.
    """
    try:
        client = _get_gcp_client(project_id)
        logs = list(client.list_logs())
        log_names = [log.name for log in logs[:50]]
        
        return json.dumps({
            "count": len(log_names),
            "log_names": log_names,
            "note": "List limited to first 50 logs." if len(logs) > 50 else ""
        }, indent=2)

    except Exception as e:
        return f"Error listing logs: {str(e)}"

@mcp.tool()
def list_monitored_resource_descriptors(project_id: Optional[str] = None) -> str:
    """
    Lists available Monitored Resource types (e.g., 'gce_instance', 'k8s_container').
    
    Args:
        project_id: The GCP Project ID.
    """
    try:
        client = _get_gcp_client(project_id)
        descriptors = client.list_resource_descriptors()
        
        resources = []
        for desc in descriptors:
            resources.append({
                "type": desc.type,
                "display_name": desc.display_name,
                "description": desc.description[:100] + "..."
            })
            if len(resources) >= 20:
                break
                
        return json.dumps(resources, indent=2)

    except Exception as e:
        return f"Error listing resource descriptors: {str(e)}"

@mcp.tool()
def get_log_entry_by_insert_id(insert_id: str, log_name: str, project_id: Optional[str] = None) -> str:
    """
    Retrieve a specific log entry by its Insert ID.
    
    Args:
        insert_id: The unique identifier of the log entry.
        log_name: The name of the log (e.g., "projects/my-project/logs/syslog").
        project_id: The GCP Project ID.
    """
    try:
        filter_str = f'insertId="{insert_id}" AND logName="{log_name}"'
        return search_logs(filter_expression=filter_str, project_id=project_id, limit=1)
    except Exception as e:
        return f"Error fetching specific log: {str(e)}"

if __name__ == "__main__":
    # Start the MCP server using SSE (Streamable HTTP) transport.
    # This will start a uvicorn server.
    # Default URL: http://0.0.0.0:8000/sse
    print("Starting GCP Logging MCP Server via SSE...")
    print("Ensure 'uvicorn' is installed: pip install uvicorn")
    mcp.run(transport="sse")
