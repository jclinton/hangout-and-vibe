#!/usr/bin/env python3
"""
Hangout and Vibe - A scaffolding for Claude to exist in its own environment.

Run with: python main.py
"""

import asyncio
import signal
import sys

import anyio

from agent import HangoutAgent
from config import ITERATION_DELAY_SECONDS


async def main():
    agent = HangoutAgent()

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        print("\n\nShutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # One-time initialization if needed
    if not agent.is_initialized:
        await agent.initialize()

    # Main loop
    print(f"\nStarting main loop (delay: {ITERATION_DELAY_SECONDS}s between iterations)")
    print("Press Ctrl+C to stop\n")

    while not shutdown_event.is_set():
        try:
            await agent.run_iteration()
        except Exception as e:
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

    print("Agent stopped.")


if __name__ == "__main__":
    anyio.run(main)
