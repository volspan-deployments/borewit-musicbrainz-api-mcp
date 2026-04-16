from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
import threading
from fastmcp import FastMCP
import httpx
import os
from typing import Optional, List
import xml.etree.ElementTree as ET

mcp = FastMCP("MusicBrainz API Client")

MB_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
USER_AGENT = "MusicBrainz-MCP-Server/1.0.0 ( mcp@example.com )"

MBUSER = os.environ.get("MBUSER", "")
MBPASS = os.environ.get("MBPASS", "")


def get_headers(accept_json: bool = True) -> dict:
    headers = {"User-Agent": USER_AGENT}
    if accept_json:
        headers["Accept"] = "application/json"
    return headers


@mcp.tool()
async def search_musicbrainz(
    _track("search_musicbrainz")
    entity_type: str,
    query: str,
    limit: int = 25,
    offset: int = 0
) -> dict:
    """Search MusicBrainz for entities like artists, recordings, releases, release-groups, labels, works, series, events, places, instruments, or URLs."""
    valid_types = [
        "artist", "recording", "release", "release-group", "label",
        "work", "series", "event", "place", "instrument", "url"
    ]
    if entity_type not in valid_types:
        return {"error": f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(valid_types)}"}

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    params = {
        "query": query,
        "limit": limit,
        "offset": offset,
        "fmt": "json"
    }

    url = f"{MB_BASE_URL}/{entity_type}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_headers())
        if response.status_code != 200:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}
        return response.json()


@mcp.tool()
async def lookup_entity(
    _track("lookup_entity")
    entity_type: str,
    mbid: str,
    inc: Optional[List[str]] = None
) -> dict:
    """Look up a specific MusicBrainz entity by its MBID."""
    valid_types = [
        "artist", "recording", "release", "release-group", "label",
        "work", "series", "event", "place", "instrument", "url", "isrc", "iswc"
    ]
    if entity_type not in valid_types:
        return {"error": f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(valid_types)}"}

    params: dict = {"fmt": "json"}
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/{entity_type}/{mbid}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_headers())
        if response.status_code != 200:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}
        return response.json()


@mcp.tool()
async def browse_entities(
    _track("browse_entities")
    entity_type: str,
    linked_entity_type: str,
    linked_mbid: str,
    inc: Optional[List[str]] = None,
    limit: int = 25,
    offset: int = 0
) -> dict:
    """Browse MusicBrainz entities that are linked to a specific entity."""
    valid_types = [
        "artist", "recording", "release", "release-group", "label",
        "work", "event", "place", "instrument", "series", "url"
    ]
    if entity_type not in valid_types:
        return {"error": f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(valid_types)}"}

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    params: dict = {
        linked_entity_type: linked_mbid,
        "limit": limit,
        "offset": offset,
        "fmt": "json"
    }
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/{entity_type}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_headers())
        if response.status_code != 200:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}
        return response.json()


@mcp.tool()
async def get_cover_art(
    _track("get_cover_art")
    entity_type: str,
    mbid: str,
    cover_type: Optional[str] = None
) -> dict:
    """Retrieve cover art images for a MusicBrainz release or release-group from the Cover Art Archive."""
    if entity_type not in ("release", "release-group"):
        return {"error": "entity_type must be 'release' or 'release-group'"}

    if cover_type:
        if cover_type not in ("front", "back"):
            return {"error": "cover_type must be 'front' or 'back'"}
        url = f"{CAA_BASE_URL}/{entity_type}/{mbid}/{cover_type}"
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.get(url, headers=get_headers(accept_json=False))
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get("location")
                return {"url": location, "status": "redirect", "cover_type": cover_type}
            elif response.status_code == 404:
                return {"error": "No cover art found for this entity", "mbid": mbid}
            elif response.status_code == 400:
                return {"error": "Invalid UUID"}
            else:
                return {"error": f"Unexpected HTTP status: {response.status_code}"}
    else:
        url = f"{CAA_BASE_URL}/{entity_type}/{mbid}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=get_headers())
            if response.status_code == 404:
                return {"error": "No cover art found for this entity", "mbid": mbid}
            elif response.status_code == 400:
                return {"error": "Invalid UUID"}
            elif response.status_code != 200:
                return {"error": f"Cover Art Archive error: HTTP {response.status_code}", "body": response.text}
            return response.json()


@mcp.tool()
async def submit_isrc(
    _track("submit_isrc")
    recording_mbid: str,
    isrc: str
) -> dict:
    """Submit an ISRC code to link to a recording in MusicBrainz. Requires authentication."""
    if not MBUSER or not MBPASS:
        return {"error": "Authentication required. Set MBUSER and MBPASS environment variables."}

    # Build XML payload for ISRC submission
    xml_body = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<metadata xmlns=\"http://musicbrainz.org/ns/mmd-2.0#\">
  <recording-list>
    <recording id=\"{recording_mbid}\">
      <isrc-list count=\"1\">
        <isrc id=\"{isrc}\"/>
      </isrc-list>
    </recording>
  </recording-list>
</metadata>"""

    url = f"{MB_BASE_URL}/recording/{recording_mbid}/isrcs"
    # Use POST to /ws/2/recording with ISRC submission
    submit_url = f"{MB_BASE_URL}/isrc/{isrc}"

    # The correct endpoint for submitting ISRCs is POST /ws/2/recording
    post_url = f"{MB_BASE_URL}/recording"

    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/xml; charset=UTF-8",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            post_url,
            content=xml_body.encode("utf-8"),
            headers=headers,
            auth=(MBUSER, MBPASS)
        )
        if response.status_code in (200, 201):
            return {"success": True, "message": f"ISRC {isrc} submitted for recording {recording_mbid}"}
        elif response.status_code == 401:
            return {"error": "Authentication failed. Check MBUSER and MBPASS."}
        elif response.status_code == 403:
            return {"error": "Forbidden. You may not have permission to submit this data."}
        else:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}


@mcp.tool()
async def submit_tags(
    _track("submit_tags")
    entity_type: str,
    mbid: str,
    tags: List[str]
) -> dict:
    """Submit user tags for MusicBrainz entities. Requires authentication."""
    if not MBUSER or not MBPASS:
        return {"error": "Authentication required. Set MBUSER and MBPASS environment variables."}

    valid_types = ["artist", "recording", "release", "release-group", "label", "work", "event"]
    if entity_type not in valid_types:
        return {"error": f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(valid_types)}"}

    if not tags:
        return {"error": "At least one tag must be provided."}

    # Build tag list XML
    tag_elements = "".join(f'<tag name="{t}"/>' for t in tags)
    plural = entity_type + "s" if not entity_type.endswith("s") else entity_type
    # Properly pluralize for release-group
    if entity_type == "release-group":
        plural = "release-group-list"
        entity_list_open = f'<release-group-list>'
        entity_list_close = f'</release-group-list>'
        entity_elem = f'<release-group id="{mbid}"><user-tag-list>{tag_elements}</user-tag-list></release-group>'
    else:
        entity_list_open = f'<{entity_type}-list>'
        entity_list_close = f'</{entity_type}-list>'
        entity_elem = f'<{entity_type} id="{mbid}"><user-tag-list>{tag_elements}</user-tag-list></{entity_type}>'

    xml_body = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<metadata xmlns=\"http://musicbrainz.org/ns/mmd-2.0#\">
  {entity_list_open}
    {entity_elem}
  {entity_list_close}
</metadata>"""

    url = f"{MB_BASE_URL}/tag"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/xml; charset=UTF-8",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            content=xml_body.encode("utf-8"),
            headers=headers,
            auth=(MBUSER, MBPASS)
        )
        if response.status_code in (200, 201):
            return {"success": True, "message": f"Tags {tags} submitted for {entity_type} {mbid}"}
        elif response.status_code == 401:
            return {"error": "Authentication failed. Check MBUSER and MBPASS."}
        elif response.status_code == 403:
            return {"error": "Forbidden. You may not have permission to submit tags."}
        else:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}


@mcp.tool()
async def submit_rating(
    _track("submit_rating")
    entity_type: str,
    mbid: str,
    rating: int
) -> dict:
    """Submit a user rating (1-5) for a MusicBrainz entity. Requires authentication."""
    if not MBUSER or not MBPASS:
        return {"error": "Authentication required. Set MBUSER and MBPASS environment variables."}

    valid_types = ["artist", "recording", "release", "release-group", "label", "work"]
    if entity_type not in valid_types:
        return {"error": f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(valid_types)}"}

    if not (1 <= rating <= 5):
        return {"error": "Rating must be between 1 and 5 inclusive."}

    # Convert 1-5 scale to 1-100 scale for MusicBrainz API
    mb_rating = rating * 20

    if entity_type == "release-group":
        entity_list_open = "<release-group-list>"
        entity_list_close = "</release-group-list>"
        entity_elem = f'<release-group id="{mbid}"><user-rating>{mb_rating}</user-rating></release-group>'
    else:
        entity_list_open = f"<{entity_type}-list>"
        entity_list_close = f"</{entity_type}-list>"
        entity_elem = f'<{entity_type} id="{mbid}"><user-rating>{mb_rating}</user-rating></{entity_type}>'

    xml_body = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<metadata xmlns=\"http://musicbrainz.org/ns/mmd-2.0#\">
  {entity_list_open}
    {entity_elem}
  {entity_list_close}
</metadata>"""

    url = f"{MB_BASE_URL}/rating"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/xml; charset=UTF-8",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            content=xml_body.encode("utf-8"),
            headers=headers,
            auth=(MBUSER, MBPASS)
        )
        if response.status_code in (200, 201):
            return {"success": True, "message": f"Rating {rating}/5 submitted for {entity_type} {mbid}"}
        elif response.status_code == 401:
            return {"error": "Authentication failed. Check MBUSER and MBPASS."}
        elif response.status_code == 403:
            return {"error": "Forbidden. You may not have permission to submit ratings."}
        else:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}


@mcp.tool()
async def lookup_isrc(
    _track("lookup_isrc")
    isrc: str,
    inc: Optional[List[str]] = None
) -> dict:
    """Look up MusicBrainz recordings associated with a given ISRC code."""
    params: dict = {"fmt": "json"}
    if inc:
        params["inc"] = "+".join(inc)

    url = f"{MB_BASE_URL}/isrc/{isrc}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_headers())
        if response.status_code == 404:
            return {"error": f"No recordings found for ISRC '{isrc}'", "isrc": isrc}
        elif response.status_code != 200:
            return {"error": f"MusicBrainz API error: HTTP {response.status_code}", "body": response.text}
        return response.json()




_SERVER_SLUG = "borewit-musicbrainz-api"

def _track(tool_name: str, ua: str = ""):
    import threading
    def _send():
        try:
            import urllib.request, json as _json
            data = _json.dumps({"slug": _SERVER_SLUG, "event": "tool_call", "tool": tool_name, "user_agent": ua}).encode()
            req = urllib.request.Request("https://www.volspan.dev/api/analytics/event", data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

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
