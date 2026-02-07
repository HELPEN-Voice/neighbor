"""
WebhookManager for Diligence Agents

This module provides a client interface for agents to interact with the webhook server.
It handles registration, waiting, and retrieval of webhook responses via WebSocket.
"""

import os
import asyncio
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime
import websockets
from websockets.exceptions import WebSocketException

logger = logging.getLogger(__name__)


class WebhookManagerClient:
    """Client for interacting with the webhook server."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._events: Dict[str, asyncio.Event] = {}
            cls._instance._results: Dict[str, Any] = {}
        return cls._instance

    def __init__(self):
        """Initialize the webhook manager client."""
        if self._initialized:
            return

        self.webhook_url = os.getenv("OPENAI_WEBHOOK_URL", "").strip('"')

        # Extract base URL from the full webhook URL
        if self.webhook_url:
            # Remove the /webhooks/openai path to get base URL
            if "/webhooks/openai" in self.webhook_url:
                self.base_url = self.webhook_url.replace("/webhooks/openai", "")
            else:
                self.base_url = self.webhook_url

            # Ensure base URL doesn't end with /
            self.base_url = self.base_url.rstrip("/")

            # Use AWS ALB for WebSocket connections
            self.ws_base_url = (
                "ws://webhook-server-alb-1412407138.us-east-2.elb.amazonaws.com"
            )
        else:
            self.base_url = ""
            self.ws_base_url = (
                "ws://webhook-server-alb-1412407138.us-east-2.elb.amazonaws.com"
            )

        self._initialized = True

    def get_webhook_url(self) -> Optional[str]:
        """Get the configured webhook URL for OpenAI."""
        return self.webhook_url if self.webhook_url else None

    async def register_callback(self, response_id: str, agent_name: str = None) -> bool:
        """Register a callback with the webhook server."""
        try:
            logger.info(
                f"📌 Registering callback for {response_id} (agent: {agent_name})"
            )

            # Create an event for this response ID
            if response_id not in self._events:
                self._events[response_id] = asyncio.Event()

            return True

        except Exception as e:
            logger.error(f"Error registering callback: {e}")
            return False

    def handle_webhook_notification(self, response_id: str, data: Any):
        """Called by the webhook server when a webhook is received."""
        logger.info(f"📥 Webhook notification received for {response_id}")

        # Store in memory
        self._results[response_id] = data

        # Set the event to wake up the waiting coroutine
        if response_id in self._events:
            self._events[response_id].set()

    async def wait_for_webhook(
        self, response_id: str, timeout: int = 2700, max_reconnects: int = 5
    ) -> Dict[str, Any]:
        """
        Wait for a webhook response using WebSocket connection.
        Automatically reconnects on connection drops with exponential backoff.
        """
        logger.info(f"⏳ Waiting for webhook for response_id: {response_id}")
        logger.info(f"📡 Webhook URL configured: {self.webhook_url}")

        ws_url = f"{self.ws_base_url}/ws/{response_id}"
        deadline = asyncio.get_event_loop().time() + timeout
        attempt = 0

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.error(f"⏰ Timeout waiting for webhook {response_id}")
                return {
                    "status": "timeout",
                    "error": f"Timeout after {timeout} seconds waiting for webhook",
                }

            try:
                async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as websocket:
                    if attempt > 0:
                        logger.info(f"🔌 Reconnected to WebSocket (attempt {attempt + 1}): {ws_url}")
                    else:
                        logger.info(f"🔌 Connected to WebSocket: {ws_url}")
                    attempt = 0  # reset on successful connect

                    while True:
                        try:
                            message = await asyncio.wait_for(
                                websocket.recv(), timeout=remaining
                            )
                            data = json.loads(message)

                            if data.get("type") == "heartbeat":
                                logger.debug(f"💓 Heartbeat received for {response_id}")
                                remaining = deadline - asyncio.get_event_loop().time()
                                continue
                            elif data.get("type") == "webhook_received":
                                logger.info(f"✅ Webhook received notification for {response_id}")
                                await asyncio.sleep(10.0)
                                return {
                                    "status": "completed",
                                    "response_id": response_id,
                                    "webhook_received": True,
                                    "data": data.get("data"),
                                }
                        except asyncio.TimeoutError:
                            logger.error(f"⏰ Timeout waiting for webhook {response_id}")
                            return {
                                "status": "timeout",
                                "error": f"Timeout after {timeout} seconds waiting for webhook",
                            }
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning(f"WebSocket connection closed for {response_id}, will reconnect...")
                            break  # break inner loop, reconnect in outer loop
                        except Exception as e:
                            logger.warning(f"Error receiving WebSocket message: {e}, will reconnect...")
                            break

            except asyncio.TimeoutError:
                logger.error(f"⏰ Timeout waiting for webhook {response_id}")
                return {
                    "status": "timeout",
                    "error": f"Timeout after {timeout} seconds waiting for webhook",
                }
            except Exception as e:
                logger.warning(f"WebSocket connection failed: {e}")

            # Reconnect with exponential backoff
            attempt += 1
            if attempt > max_reconnects:
                logger.error(f"❌ Exhausted {max_reconnects} reconnect attempts for {response_id}")
                return {
                    "status": "error",
                    "error": f"WebSocket failed after {max_reconnects} reconnect attempts",
                }
            backoff = min(2 ** attempt, 30)
            logger.info(f"🔄 Reconnecting in {backoff}s (attempt {attempt}/{max_reconnects})...")
            await asyncio.sleep(backoff)

    async def retrieve_response(self, response_id: str) -> Dict[str, Any]:
        """
        Retrieve the full response data from OpenAI after webhook notification.

        This should be called after wait_for_webhook indicates completion.
        """
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI()
            response = await client.responses.retrieve(response_id)

            # Check if response is still processing
            status = getattr(response, "status", "unknown")
            if status != "completed":
                logger.warning(
                    f"Response status is '{status}', not 'completed'. Response ID: {response_id}"
                )
                # If still processing, return a pending status
                if status in ["queued", "in_progress"]:
                    return {
                        "status": "pending",
                        "raw_output": f"Research still processing (status: {status})",
                        "citations": [],
                        "research_steps": [],
                    }

            # Process the response
            if not response.output:
                # Log more details about the response
                logger.error(f"Response has no output. Status: {status}")
                logger.error(f"Response ID: {response_id}")
                logger.error(f"Response attributes: {dir(response)}")
                raise ValueError("No output in response")

            output_text = response.output[-1].content[0].text

            # Extract citations
            annotations = response.output[-1].content[0].annotations
            citations = []
            for i, citation in enumerate(annotations):
                citations.append(
                    {
                        "index": i + 1,
                        "title": citation.title,
                        "url": citation.url,
                        "start_index": citation.start_index,
                        "end_index": citation.end_index,
                    }
                )

            # Extract research steps
            research_steps = []
            for item in response.output:
                if item.type == "reasoning":
                    reasoning_step = {"type": "reasoning", "summary": []}
                    if hasattr(item, "summary"):
                        for s in item.summary:
                            reasoning_step["summary"].append(s.text)
                    research_steps.append(reasoning_step)

                elif item.type == "web_search_call":
                    query = ""
                    if hasattr(item, "action") and item.action:
                        if hasattr(item.action, "query"):
                            query = item.action.query
                        elif hasattr(item.action, "get"):
                            query = item.action.get("query", "")

                    search = {
                        "type": "web_search",
                        "query": query,
                        "status": item.status if hasattr(item, "status") else "",
                    }
                    research_steps.append(search)

            return {
                "raw_output": output_text,
                "citations": citations,
                "research_steps": research_steps,
                "status": "completed",
            }

        except Exception as e:
            logger.error(f"Error retrieving response {response_id}: {e}")
            return {
                "status": "error",
                "error": str(e),
                "raw_output": f"Error retrieving response: {str(e)}",
                "citations": [],
                "research_steps": [],
            }

    def clear(self):
        """Clear all cached events and results."""
        self._events.clear()
        self._results.clear()
        logger.info("🧹 Cleared webhook manager client cache")

    def is_webhook_configured(self) -> bool:
        """Check if webhook URL is properly configured."""
        return bool(self.webhook_url)


# Global singleton instance
webhook_manager = WebhookManagerClient()
