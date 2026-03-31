"""
Web tools — internet search and webpage reading.
"""

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from ddgs import DDGS
from tools.registry import tool


@tool(
    name="read_webpage",
    tags=["network", "read"],
    description="Read webpage content and convert it to readable text. Use this to view specific web pages from search results, read documentation, GitHub READMEs, tech blogs, etc.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the webpage to read.",
            }
        },
        "required": ["url"],
    },
)
def read_webpage(url: str) -> str:
    """Read webpage content and convert to readable text."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Convert to Markdown (most LLM-friendly format)
        text = md(str(soup), strip=["img"])
        # Clean up extra blank lines
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)

        return f"Webpage content ({url}):\n\n{text}"

    except Exception as e:
        return f"Failed to read webpage: {str(e)}"


@tool(
    name="web_search",
    tags=["network", "read"],
    description="Search the internet using a search engine. Use this when you need to find general (non-academic) information such as tool documentation, tech blogs, error solutions, open-source project info, etc.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keywords.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return. Default is 5.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)
def web_search(query: str, max_results: int = 5) -> str:
    """Search the internet for information."""
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"No results found for '{query}'."

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"[{i}] {r['title']}\n"
                f"    URL: {r['href']}\n"
                f"    Snippet: {r['body']}"
            )

        return "\n\n".join(formatted)

    except Exception as e:
        return f"Search failed: {str(e)}"
