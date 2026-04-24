"""
server.py — MediaWiki MCP Server

This is the entry point. It:
  1. Loads config from environment / .env
  2. Creates a WikiClient (which logs in immediately)
  3. Registers tools that Claude can call
  4. Starts the MCP server over stdio (the standard transport for local MCP servers)

What is MCP?
  The Model Context Protocol lets Claude call "tools" exposed by external servers.
  Each tool has:
    - a name     (what Claude calls it)
    - a description (what Claude reads to decide WHEN to use it)
    - an inputSchema (JSON Schema defining the parameters)
  
  When Claude decides to use a tool, the MCP runtime calls our handler,
  passes the arguments, and returns the result back to Claude as a ToolResult.
"""

import json
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from config import get_config
from wiki_client import WikiClient


# ---------------------------------------------------------------------------
# Initialise
# ---------------------------------------------------------------------------

config = get_config()

# WikiClient logs in on construction — if credentials are wrong, we fail here
wiki = WikiClient(
    base_url=config["WIKI_URL"],
    username=config["WIKI_USERNAME"],
    password=config["WIKI_PASSWORD"],
    totp_secret=config["WIKI_TOTP_SECRET"],
)

# The Server object is the core MCP server. We give it a name and version
# that shows up in Claude's tool list.
app = Server("mediawiki-mcp")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------
# The @app.list_tools() decorator registers a function that returns the list
# of available tools. Claude reads this list to know what it can call.

WRITE_TOOLS = {"wiki_edit_page", "wiki_section_edit", "wiki_append_to_page"}


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="wiki_search",
            description=(
                "Search the internal MediaWiki documentation. "
                "Returns a list of matching page titles and text snippets. "
                "Use this first to find relevant pages before reading them."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query, e.g. 'HAProxy SSL configuration'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results to return (default 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),

        Tool(
            name="wiki_get_page",
            description=(
                "Fetch the full wikitext content of a specific wiki page by its exact title. "
                "Use wiki_search first to find the correct title."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The exact page title, e.g. 'HAProxy/SSL Termination'",
                    },
                },
                "required": ["title"],
            },
        ),

        Tool(
            name="wiki_list_pages",
            description=(
                "List wiki page titles, optionally filtered by a title prefix. "
                "Useful for browsing a section of the wiki, e.g. all pages under 'Networking/'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Title prefix filter, e.g. 'Networking/' (optional)",
                        "default": "",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
            },
        ),

        Tool(
            name="wiki_get_sections",
            description=(
                "List the sections of a wiki page with their index numbers and heading titles. "
                "Use this before wiki_section_edit to find the correct section index."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Page title to inspect",
                    },
                },
                "required": ["title"],
            },
        ),
    ]

    if config["WIKI_ALLOW_WRITE"]:
        tools += [
            Tool(
                name="wiki_edit_page",
                description=(
                    "Create or fully replace a wiki page with new wikitext content. "
                    "WARNING: This overwrites the entire page. "
                    "Use wiki_get_page first to read the current content, then edit as needed. "
                    "Always include a meaningful edit summary."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Page title to create or overwrite",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full wikitext content for the page",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Edit summary shown in page history",
                            "default": "Edited via MCP",
                        },
                    },
                    "required": ["title", "content"],
                },
            ),

            Tool(
                name="wiki_section_edit",
                description=(
                    "Replace the content of a single section of a wiki page by section index. "
                    "Much safer than wiki_edit_page because it only touches one section. "
                    "Use wiki_get_sections first to find the right index, then wiki_get_page to read the current wikitext."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Page title",
                        },
                        "section": {
                            "type": "integer",
                            "description": "Zero-based section index. 0 = lead text, 1 = first heading, etc.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full wikitext replacement for that section (include the == Heading == line)",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Edit summary shown in page history",
                            "default": "Edited via MCP",
                        },
                    },
                    "required": ["title", "section", "content"],
                },
            ),

            Tool(
                name="wiki_append_to_page",
                description=(
                    "Append text to the END of an existing wiki page without overwriting it. "
                    "Ideal for adding new entries to log pages, runbooks, or changelogs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Page title to append to",
                        },
                        "content": {
                            "type": "string",
                            "description": "Wikitext to append at the end of the page",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Edit summary shown in page history",
                            "default": "Appended via MCP",
                        },
                    },
                    "required": ["title", "content"],
                },
            ),
        ]

    return tools


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
# The @app.call_tool() decorator registers the function Claude calls when
# it decides to use one of our tools. We dispatch by tool name.
#
# All handlers must return a list of Content objects. TextContent wraps a
# plain string. We serialise dicts to JSON so Claude can parse them.

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Dispatch incoming tool calls to the appropriate WikiClient method.
    
    MCP calls this function with:
      name      — the tool name Claude selected
      arguments — a dict matching the tool's inputSchema
    
    We return a list of TextContent (there's usually just one item).
    Claude reads the text as the "result" of the tool call.
    """

    if name == "wiki_search":
        results = wiki.search(
            query=arguments["query"],
            limit=arguments.get("limit", 10),
        )
        if not results:
            return [TextContent(type="text", text="No results found.")]
        # Return as JSON so Claude can parse titles/snippets cleanly
        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "wiki_get_page":
        page = wiki.get_page(title=arguments["title"])
        if not page["exists"]:
            return [TextContent(type="text", text=f"Page '{arguments['title']}' does not exist.")]
        return [TextContent(type="text", text=page["content"])]

    elif name == "wiki_list_pages":
        titles = wiki.list_pages(
            prefix=arguments.get("prefix", ""),
            limit=arguments.get("limit", 20),
        )
        if not titles:
            return [TextContent(type="text", text="No pages found.")]
        return [TextContent(type="text", text="\n".join(titles))]

    elif name in WRITE_TOOLS and not config["WIKI_ALLOW_WRITE"]:
        return [TextContent(type="text", text=f"Tool '{name}' is disabled. Set WIKI_ALLOW_WRITE=true to enable write access.")]

    elif name == "wiki_edit_page":
        result = wiki.edit_page(
            title=arguments["title"],
            content=arguments["content"],
            summary=arguments.get("summary", "Edited via MCP"),
        )
        return [TextContent(type="text", text=f"Edit successful: {json.dumps(result)}")]

    elif name == "wiki_get_sections":
        sections = wiki.get_page_sections(title=arguments["title"])
        return [TextContent(type="text", text=json.dumps(sections, indent=2))]

    elif name == "wiki_section_edit":
        result = wiki.section_edit(
            title=arguments["title"],
            section=arguments["section"],
            content=arguments["content"],
            summary=arguments.get("summary", "Edited via MCP"),
        )
        return [TextContent(type="text", text=f"Section edit successful: {json.dumps(result)}")]

    elif name == "wiki_append_to_page":
        result = wiki.append_to_page(
            title=arguments["title"],
            content=arguments["content"],
            summary=arguments.get("summary", "Appended via MCP"),
        )
        return [TextContent(type="text", text=f"Append successful: {json.dumps(result)}")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """
    Start the MCP server using stdio transport.
    
    stdio is the standard transport for local MCP servers — Claude.ai /
    Claude Desktop communicates with the server by reading/writing JSON
    over stdin/stdout. This means the server runs as a subprocess.
    """
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
