"""Custom MCP tools for image fetching and vision capabilities."""

import base64
import io
import logging
from typing import Any

import aiohttp
from PIL import Image

from claude_agent_sdk import tool, create_sdk_mcp_server

logger = logging.getLogger("hangout")

# Maximum raw image size to even attempt downloading (10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024

# Maximum dimension (long edge) before resizing
# Claude auto-downscales images with long edge > 1568px anyway
# Resizing client-side saves bandwidth and improves TTFT
MAX_DIMENSION = 1568

# JPEG quality for resized images
JPEG_QUALITY = 85

# Supported image MIME types
SUPPORTED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Map MIME types to PIL format names
MIME_TO_PIL_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}


def resize_image_if_needed(image_data: bytes, content_type: str) -> tuple[bytes, str]:
    """Resize an image if it exceeds MAX_DIMENSION on its long edge.

    Claude auto-downscales images with long edge > 1568px anyway, so resizing
    client-side saves bandwidth and improves time-to-first-token.

    Args:
        image_data: Raw image bytes
        content_type: MIME type of the image

    Returns:
        Tuple of (possibly resized image bytes, output MIME type)
    """
    # Open image with PIL
    img = Image.open(io.BytesIO(image_data))
    width, height = img.size
    long_edge = max(width, height)

    # If already within limits, return as-is
    if long_edge <= MAX_DIMENSION:
        logger.info(f"Image {width}x{height} within limits, no resize needed")
        return image_data, content_type

    # Calculate scale factor to fit within MAX_DIMENSION
    scale = MAX_DIMENSION / long_edge
    new_width = int(width * scale)
    new_height = int(height * scale)

    logger.info(f"Resizing image: {width}x{height} -> {new_width}x{new_height}")

    # Convert to RGB if necessary (for JPEG output)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
        output_format = "JPEG"
        output_mime = "image/jpeg"
    else:
        output_format = MIME_TO_PIL_FORMAT.get(content_type, "JPEG")
        output_mime = content_type if content_type in SUPPORTED_TYPES else "image/jpeg"

    # Resize image
    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Encode to bytes
    buffer = io.BytesIO()
    if output_format == "JPEG":
        resized.save(buffer, format=output_format, quality=JPEG_QUALITY, optimize=True)
    else:
        resized.save(buffer, format=output_format, optimize=True)

    buffer.seek(0)
    result_bytes = buffer.read()
    logger.info(f"Resized: {len(image_data)} -> {len(result_bytes)} bytes")
    return result_bytes, output_mime


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

                # Check content length if available (reject very large downloads)
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_DOWNLOAD_SIZE:
                    error_msg = f"Image too large to download: {int(content_length)} bytes (max {MAX_DOWNLOAD_SIZE})"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Read image data
                image_data = await response.read()
                if len(image_data) > MAX_DOWNLOAD_SIZE:
                    error_msg = f"Image too large: {len(image_data)} bytes (max {MAX_DOWNLOAD_SIZE})"
                    logger.warning(f"FetchImage: {error_msg}")
                    return {
                        "content": [{"type": "text", "text": error_msg}],
                        "is_error": True,
                    }

                # Resize if needed (images > 1568px long edge)
                image_data, content_type = resize_image_if_needed(image_data, content_type)

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
