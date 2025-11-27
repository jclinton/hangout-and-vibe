#!/usr/bin/env python3
"""
Hangout and Vibe - A scaffolding for Claude to exist in its own environment.

Run with: python main.py
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

import anyio

from agent import HangoutAgent
from config import ITERATION_DELAY_SECONDS, DATA_DIR

# Set up logging
LOG_FILE = DATA_DIR / "agent.log"


def setup_logging():
    """Configure logging to both console and file."""
    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root logger for our app
    logger = logging.getLogger("hangout")
    logger.setLevel(logging.DEBUG)

    # Console handler - INFO level (less verbose)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler - DEBUG level (full detail)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info(f"Logging initialized. Log file: {LOG_FILE}")
    return logger


async def main():
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("Hangout and Vibe starting up")
    logger.info("=" * 60)

    agent = HangoutAgent()

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        print("\n\nShutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run diagnostics first to verify Discord connectivity
    logger.info("Running Discord diagnostics...")
    print("\n=== Running Discord Diagnostics ===\n")
    await agent.run_diagnostics()

    # One-time initialization if needed
    if not agent.is_initialized:
        await agent.initialize()

    # Main loop
    logger.info(f"Starting main loop (delay: {ITERATION_DELAY_SECONDS}s between iterations)")
    print(f"\nStarting main loop (delay: {ITERATION_DELAY_SECONDS}s between iterations)")
    print("Press Ctrl+C to stop\n")

    iteration_count = 0
    while not shutdown_event.is_set():
        iteration_count += 1
        logger.info(f"--- Iteration {iteration_count} ---")
        try:
            await agent.run_iteration()
        except Exception as e:
            logger.exception(f"Error in iteration {iteration_count}: {e}")
            print(f"\n[Error in iteration: {e}]")
            # Continue running despite errors

        # Wait before next iteration, but allow early exit on shutdown
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=ITERATION_DELAY_SECONDS,
            )
        except asyncio.TimeoutError:
            pass  # Normal case - timeout means continue to next iteration

    logger.info("Agent stopped.")
    print("Agent stopped.")


if __name__ == "__main__":
    anyio.run(main)
