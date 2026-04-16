from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
import threading
from fastmcp import FastMCP
import httpx
import os
import json
from typing import Optional, List

mcp = FastMCP("MusicBrainz API Client")

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
MB_USER_AGENT = os.environ.get("MBUSER", "MusicBrainzMCPClient/1.0")

DEFAULT_HEADERS = {
    "User-Agent": MB_USER_AGENT,
    "Accept": "application/json",
}


@mcp.tool()
async def search_musicbrainz(
    entity: str,
    query: str,
    limit: int = 25,
    offset: int = 0
) -> dict:
    """Search the MusicBrainz database for entities such as artists, recordings, releases, release-groups, labels, works, or series."""
    valid_entities = [
        "artist", "recording", "release", "release-group",
        "label", "work", "series", "area", "instrument", "event", "url"
    ]
    if entity not in valid_entities:
        return {"error": f"Invalid entity type '{entity}'. Must be one of: {', '.join(valid_entities)}"}

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    params = {
        "query": query,
        "limit": limit,
        "offset": offset,
        "fmt": "json"
    }

    url = f"{MB_BASE_URL}/{entity}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=DEFAULT_HEADERS)
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}
        return response.json()


@mcp.tool()
async def get_musicbrainz_entity(
    entity: str,
    mbid: str,
    inc: Optional[List[str]] = None
) -> dict:
    """Retrieve detailed metadata for a specific MusicBrainz entity by its MBID."""
    valid_entities = [
        "artist", "recording", "release", "release-group",
        "label", "work", "series", "area", "instrument", "event", "url", "place"
    ]
    if entity not in valid_entities:
        return {"error": f"Invalid entity type '{entity}'. Must be one of: {', '.join(valid_entities)}"}

    params = {"fmt": "json"}
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/{entity}/{mbid}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=DEFAULT_HEADERS)
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}
        return response.json()


@mcp.tool()
async def lookup_by_isrc(
    isrc: str,
    inc: Optional[List[str]] = None
) -> dict:
    """Look up MusicBrainz recordings associated with a given ISRC (International Standard Recording Code)."""
    params = {"fmt": "json"}
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/isrc/{isrc}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=DEFAULT_HEADERS)
        if response.status_code == 404:
            return {"error": "ISRC not found", "isrc": isrc}
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}
        return response.json()


@mcp.tool()
async def get_cover_art(
    entity: str,
    mbid: str,
    cover_type: Optional[str] = None
) -> dict:
    """Retrieve cover art information for a MusicBrainz release or release-group from the Cover Art Archive."""
    if entity not in ("release", "release-group"):
        return {"error": "entity must be 'release' or 'release-group'"}

    if cover_type and cover_type not in ("front", "back"):
        return {"error": "cover_type must be 'front' or 'back' if specified"}

    if cover_type:
        url = f"{CAA_BASE_URL}/{entity}/{mbid}/{cover_type}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"User-Agent": MB_USER_AGENT})
            if response.status_code in (301, 302, 307, 308):
                redirect_url = response.headers.get("location") or response.headers.get("Location")
                return {"url": redirect_url, "status": "redirect", "cover_type": cover_type}
            elif response.status_code == 404:
                return {"error": f"No {cover_type} cover found for {entity} {mbid}"}
            elif response.status_code == 200:
                return {"url": str(response.url), "status": "ok", "cover_type": cover_type}
            else:
                return {"error": f"HTTP {response.status_code}", "detail": response.text}
    else:
        url = f"{CAA_BASE_URL}/{entity}/{mbid}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers={"User-Agent": MB_USER_AGENT, "Accept": "application/json"}
            )
            if response.status_code == 404:
                return {"error": f"No cover art found for {entity} {mbid}"}
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}", "detail": response.text}
            return response.json()


@mcp.tool()
async def submit_isrc(
    recording_mbid: str,
    isrcs: List[str]
) -> dict:
    """Submit one or more ISRC codes linked to MusicBrainz recording MBIDs. Requires authentication."""
    mb_username = os.environ.get("MB_USERNAME")
    mb_password = os.environ.get("MB_PASSWORD")

    if not mb_username or not mb_password:
        return {
            "error": "Authentication required. Set MB_USERNAME and MB_PASSWORD environment variables."
        }

    if not isrcs:
        return {"error": "No ISRCs provided"}

    # Build XML payload for ISRC submission
    isrc_xml_parts = "".join(
        f'<isrc id="{isrc}"/>'
        for isrc in isrcs
    )
    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<metadata xmlns="http://musicbrainz.org/ns/mmd-2.0#">'
        '<recording-list>'
        f'<recording id="{recording_mbid}">'
        f'<isrc-list count="{len(isrcs)}">'
        f'{isrc_xml_parts}'
        '</isrc-list>'
        '</recording>'
        '</recording-list>'
        '</metadata>'
    )

    url = f"{MB_BASE_URL}/recording/"
    headers = {
        "User-Agent": MB_USER_AGENT,
        "Content-Type": "application/xml; charset=UTF-8",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # First request to get digest auth challenge
        response = await client.post(
            url,
            content=xml_body.encode("utf-8"),
            headers=headers,
            params={"client": MB_USER_AGENT},
            auth=httpx.DigestAuth(mb_username, mb_password)
        )
        if response.status_code in (200, 201):
            return {"success": True, "message": "ISRCs submitted successfully", "recording_mbid": recording_mbid, "isrcs": isrcs}
        else:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}


@mcp.tool()
async def lookup_by_iswc(
    iswc: str,
    inc: Optional[List[str]] = None
) -> dict:
    """Look up MusicBrainz works associated with a given ISWC (International Standard Musical Work Code)."""
    params = {"fmt": "json"}
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/iswc/{iswc}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=DEFAULT_HEADERS)
        if response.status_code == 404:
            return {"error": "ISWC not found", "iswc": iswc}
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}
        return response.json()


@mcp.tool()
async def post_musicbrainz_edit(
    entity: str,
    mbid: str,
    edit_data: str,
    edit_note: Optional[str] = None
) -> dict:
    """Submit an authenticated edit to the MusicBrainz database. Requires authentication."""
    mb_username = os.environ.get("MB_USERNAME")
    mb_password = os.environ.get("MB_PASSWORD")

    if not mb_username or not mb_password:
        return {
            "error": "Authentication required. Set MB_USERNAME and MB_PASSWORD environment variables."
        }

    valid_entities = ["recording", "release", "artist", "label", "release-group", "work"]
    if entity not in valid_entities:
        return {"error": f"Invalid entity type '{entity}'. Must be one of: {', '.join(valid_entities)}"}

    try:
        data = json.loads(edit_data)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in edit_data: {str(e)}"}

    if edit_note:
        data["edit_note"] = edit_note

    url = f"{MB_BASE_URL}/{entity}/{mbid}"
    headers = {
        "User-Agent": MB_USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=data,
            headers=headers,
            params={"fmt": "json", "client": MB_USER_AGENT},
            auth=httpx.DigestAuth(mb_username, mb_password)
        )
        if response.status_code in (200, 201):
            try:
                return {"success": True, "response": response.json()}
            except Exception:
                return {"success": True, "message": "Edit submitted successfully"}
        else:
            return {"error": f"HTTP {response.status_code}", "detail": response.text}




_SERVER_SLUG = "borewit-musicbrainz-api"

def _track(tool_name: str, ua: str = ""):
    try:
        import urllib.request, json as _json
        data = _json.dumps({"slug": _SERVER_SLUG, "event": "tool_call", "tool": tool_name, "user_agent": ua}).encode()
        req = urllib.request.Request("https://www.volspan.dev/api/analytics/event", data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass

async def health(request):
    return JSONResponse({"status": "ok", "server": mcp.name})

async def tools(request):
    registered = await mcp.list_tools()
    tool_list = [{"name": t.name, "description": t.description or ""} for t in registered]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})

sse_app = mcp.http_app(transport="sse")

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/tools", tools),
        Mount("/", sse_app),
    ],
    lifespan=sse_app.lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
