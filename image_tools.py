"""Custom MCP tools for image fetching and vision capabilities."""

import base64
import logging
from typing import Any

import aiohttp

from claude_agent_sdk import tool, create_sdk_mcp_server

logger = logging.getLogger("hangout")

# Maximum image size to fetch (10 MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024

# Supported image MIME types
SUPPORTED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


@tool(
    "fetch_image",
    "Fetch an image from a URL and return it for visual analysis. "
    "Use this to view images from Discord attachments, web URLs, etc. "
    "The image will be returned in a format that allows Claude to see and describe it.",
    {"url": str},
)
async def fetch_image(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch an image from a URL and return it as base64 for vision analysis.

    Args:
        args: Dictionary with 'url' key containing the image URL

    Returns:
        Dictionary with 'content' containing either:
        - An image content block if successful
        - A text error message if failed
    """
    url = args.get("url", "")
    if not url:
        return {
            "content": [{"type": "text", "text": "Error: No URL provided"}],
            "is_error": True,
        }

    logger.info(f"FetchImage: Fetching {url[:100]}...")

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    error_msg = f"Failed to fetch image: HTTP {response.status}"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Check content type
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type not in SUPPORTED_TYPES:
                    error_msg = f"Unsupported content type: {content_type}. Supported: {', '.join(SUPPORTED_TYPES)}"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Check content length if available
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_IMAGE_SIZE:
                    error_msg = f"Image too large: {int(content_length)} bytes (max {MAX_IMAGE_SIZE})"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Read image data
                image_data = await response.read()
                if len(image_data) > MAX_IMAGE_SIZE:
                    error_msg = f"Image too large: {len(image_data)} bytes (max {MAX_IMAGE_SIZE})"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Encode as base64
                base64_data = base64.b64encode(image_data).decode("utf-8")
                logger.info(f"FetchImage: Success - {len(image_data)} bytes, {content_type}")

                # Return image content for Claude's vision
                return {
                    "content": [
                        {
                            "type": "image",
                            "data": base64_data,
                            "mimeType": content_type,
                        }
                    ]
                }

    except aiohttp.ClientError as e:
        error_msg = f"Network error fetching image: {e}"
        logger.error(f"FetchImage: {error_msg}")
        return {
            "content": [{"type": "text", "text": error_msg}],
            "is_error": True,
        }
    except Exception as e:
        error_msg = f"Unexpected error fetching image: {e}"
        logger.error(f"FetchImage: {error_msg}")
        return {
            "content": [{"type": "text", "text": error_msg}],
            "is_error": True,
        }


# Create the MCP server with the image tool
image_mcp_server = create_sdk_mcp_server(
    name="image_tools",
    version="1.0.0",
    tools=[fetch_image],
)
