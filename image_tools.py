"""Custom MCP tools for image fetching and vision capabilities."""

import base64
import io
import logging
from typing import Any

import aiohttp
from PIL import Image

from claude_agent_sdk import tool, create_sdk_mcp_server

logger = logging.getLogger("hangout")

# Maximum base64 size after encoding (SDK has 1MB JSON buffer limit)
# Leave headroom for JSON wrapper, so target ~700KB base64
MAX_BASE64_SIZE = 700 * 1024  # 700 KB

# Maximum raw image size to even attempt downloading (10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024

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


def resize_image_to_fit(image_data: bytes, content_type: str, max_base64_size: int) -> tuple[bytes, str]:
    """Resize an image to fit within the base64 size limit.

    Args:
        image_data: Raw image bytes
        content_type: MIME type of the image
        max_base64_size: Maximum allowed size after base64 encoding

    Returns:
        Tuple of (resized image bytes, output MIME type)
    """
    # Calculate max raw size (base64 expands by ~33%)
    max_raw_size = int(max_base64_size * 0.75)

    # If already small enough, return as-is
    if len(image_data) <= max_raw_size:
        return image_data, content_type

    logger.info(f"Image too large ({len(image_data)} bytes), resizing...")

    # Open image with PIL
    img = Image.open(io.BytesIO(image_data))

    # Convert to RGB if necessary (for JPEG output)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
        output_format = "JPEG"
        output_mime = "image/jpeg"
    else:
        output_format = MIME_TO_PIL_FORMAT.get(content_type, "JPEG")
        output_mime = content_type if content_type in SUPPORTED_TYPES else "image/jpeg"

    # Binary search for the right scale factor
    original_size = img.size
    scale = 1.0
    min_scale = 0.1
    max_scale = 1.0

    for _ in range(10):  # Max 10 iterations
        scale = (min_scale + max_scale) / 2
        new_size = (int(original_size[0] * scale), int(original_size[1] * scale))

        # Resize image
        resized = img.resize(new_size, Image.Resampling.LANCZOS)

        # Encode to bytes
        buffer = io.BytesIO()
        if output_format == "JPEG":
            resized.save(buffer, format=output_format, quality=JPEG_QUALITY, optimize=True)
        else:
            resized.save(buffer, format=output_format, optimize=True)

        result_size = buffer.tell()

        if result_size <= max_raw_size:
            if result_size > max_raw_size * 0.8:  # Good enough, within 80-100% of target
                break
            min_scale = scale  # Can afford to be larger
        else:
            max_scale = scale  # Need to be smaller

    buffer.seek(0)
    result_bytes = buffer.read()
    logger.info(f"Resized image: {original_size} -> {new_size}, {len(image_data)} -> {len(result_bytes)} bytes")
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

                # Resize if needed to fit within SDK buffer limits
                image_data, content_type = resize_image_to_fit(image_data, content_type, MAX_BASE64_SIZE)

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
