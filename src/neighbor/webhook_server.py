#!/usr/bin/env python3
"""
Centralized OpenAI Webhook Server for Neighbor Research Agents

This server handles all OpenAI webhook callbacks for deep research agents.
Run this server independently before running any neighbor pipelines or tests.

Usage:
    python webhook_server.py --port 8080

Then configure ngrok and update .env:
    OPENAI_WEBHOOK_URL=https://your-ngrok-url.ngrok-free.app/webhooks/openai
"""

import os
import sys
import asyncio
import argparse
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import (
    FastAPI,
    Request,
    Response,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
import uvicorn
from pyngrok import ngrok, conf


# Load environment variables from .env file
def load_env_file():
    """Load environment variables from .env file"""
    script_dir = os.path.dirname(__file__)
    # From neighbor folder, go up to project root
    project_root = os.path.abspath(os.path.join(script_dir, "../../../../"))
    env_path = os.path.join(project_root, ".env")

    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Remove quotes if present
                    value = value.strip('"').strip("'")
                    os.environ[key] = value


load_env_file()

# Add parent directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "../../../.."))

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="OpenAI Webhook Server")

# Global storage for webhook responses and callbacks
webhook_responses: Dict[str, Any] = {}
webhook_callbacks: Dict[str, asyncio.Event] = {}
webhook_data: Dict[str, Any] = {}


class WebhookManager:
    """Singleton manager for webhook callbacks and responses."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.responses = {}
            cls._instance.callbacks = {}
            cls._instance.data = {}
            cls._instance.websocket_clients = {}  # Map response_id to WebSocket
        return cls._instance

    def register_callback(self, response_id: str, agent_name: str = None) -> None:
        """Register a callback for a response ID."""
        logger.info(
            f"📌 Registering webhook callback for {response_id} (agent: {agent_name})"
        )
        self.callbacks[response_id] = asyncio.Event()
        self.data[response_id] = {"agent": agent_name, "status": "pending"}

    async def wait_for_webhook(
        self, response_id: str, timeout: int = 1800
    ) -> Dict[str, Any]:
        """Wait for a webhook callback for the given response ID."""
        if response_id not in self.callbacks:
            self.register_callback(response_id)

        logger.info(f"⏳ Waiting for webhook for {response_id} (timeout: {timeout}s)")

        try:
            # Wait for the webhook with timeout
            await asyncio.wait_for(self.callbacks[response_id].wait(), timeout=timeout)

            # Return the stored response
            if response_id in self.responses:
                logger.info(f"✅ Webhook received for {response_id}")
                return self.responses[response_id]
            else:
                return {
                    "status": "error",
                    "error": "Webhook received but no data stored",
                }

        except asyncio.TimeoutError:
            logger.error(f"⏰ Timeout waiting for webhook {response_id}")
            return {
                "status": "timeout",
                "error": f"Timeout after {timeout} seconds waiting for webhook",
            }

    async def handle_webhook(
        self, response_id: str, event_type: str, data: Any
    ) -> None:
        """Handle an incoming webhook for a response ID."""
        logger.info(f"📥 Handling webhook for {response_id}: {event_type}")

        # Store the response data
        self.responses[response_id] = {
            "status": "completed" if event_type == "response.completed" else event_type,
            "event_type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        # Update data status
        if response_id in self.data:
            self.data[response_id]["status"] = event_type
            self.data[response_id]["completed_at"] = datetime.now().isoformat()

        # Notify WebSocket clients waiting for this response
        if response_id in self.websocket_clients:
            websocket = self.websocket_clients[response_id]
            try:
                await websocket.send_json(
                    {
                        "type": "webhook_received",
                        "response_id": response_id,
                        "event_type": event_type,
                        "data": data,
                    }
                )
                logger.info(f"✅ Notified WebSocket client for {response_id}")
                # Clean up after notification
                del self.websocket_clients[response_id]
            except Exception as e:
                logger.warning(f"⚠️ Could not notify WebSocket client: {e}")
                # Clean up broken connection
                if response_id in self.websocket_clients:
                    del self.websocket_clients[response_id]

        # Trigger the callback event (for backward compatibility)
        if response_id in self.callbacks:
            self.callbacks[response_id].set()
            logger.info(f"✅ Triggered callback for {response_id}")
        else:
            logger.info(
                f"📝 No server callback registered for {response_id} (client may be waiting)"
            )

    def get_status(self, response_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a response."""
        if response_id in self.data:
            return self.data[response_id]
        return None

    def cleanup_old_responses(self, max_age_seconds: int = 3600) -> int:
        """Clean up old responses to prevent memory buildup."""
        # TODO: Implement cleanup based on timestamp
        return 0


# Create singleton instance
webhook_manager = WebhookManager()


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "OpenAI Webhook Server",
        "pending_callbacks": len(webhook_manager.callbacks),
        "stored_responses": len(webhook_manager.responses),
    }


@app.get("/webhooks/status/{response_id}")
async def get_webhook_status(response_id: str):
    """Check the status of a webhook response."""
    status = webhook_manager.get_status(response_id)
    if status:
        return status
    return {"error": "Response ID not found"}


@app.websocket("/ws/{response_id}")
async def websocket_endpoint(websocket: WebSocket, response_id: str):
    """WebSocket endpoint for real-time webhook notifications with heartbeat."""
    await websocket.accept()
    logger.info(f"🔌 WebSocket client connected for {response_id}")

    # Register this WebSocket for the response ID
    webhook_manager.websocket_clients[response_id] = websocket

    async def send_heartbeat():
        """Send periodic heartbeat to keep connection alive through ALB/proxies."""
        try:
            while True:
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds
                await websocket.send_json(
                    {"type": "heartbeat", "timestamp": datetime.now().isoformat()}
                )
                logger.debug(f"💓 Sent heartbeat for {response_id}")
        except Exception:
            pass  # Connection closed, stop heartbeat

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(send_heartbeat())

    try:
        # Check if we already have the response
        if response_id in webhook_manager.responses:
            await websocket.send_json(
                {
                    "type": "webhook_already_received",
                    "response_id": response_id,
                    "data": webhook_manager.responses[response_id],
                }
            )
            await websocket.close()
            return

        # Keep connection open and wait for webhook or client disconnect
        while True:
            try:
                # Wait for any client message
                message = await websocket.receive_text()
                # Client can send "ping" and get "pong" back if needed
                if message == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket client disconnected for {response_id}")
    finally:
        # Cancel heartbeat and clean up
        heartbeat_task.cancel()
        if response_id in webhook_manager.websocket_clients:
            del webhook_manager.websocket_clients[response_id]


@app.post("/webhooks/openai")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive and process OpenAI webhooks.

    This endpoint handles all webhook events from OpenAI's deep research API.
    """
    try:
        logger.info(f"📥 WEBHOOK RECEIVED at {datetime.now().strftime('%H:%M:%S')}")

        # Get raw body and headers for signature verification
        body = await request.body()
        headers = dict(request.headers)

        # Get webhook secret from environment
        webhook_secret = os.getenv("OPENAI_WEBHOOK_SECRET", "").strip('"')

        # Check if signature headers are present
        has_signature_headers = "webhook-signature" in headers

        if webhook_secret and has_signature_headers:
            try:
                from openai import OpenAI, InvalidWebhookSignatureError

                client = OpenAI(webhook_secret=webhook_secret)

                # Verify webhook signature
                event = client.webhooks.unwrap(body, headers)

                logger.info(f"✅ Webhook signature verified")
                logger.info(f"📥 Event type: {event.type}")
                logger.info(f"📥 Event ID: {event.id}")
                logger.info(f"📥 Created at: {event.created_at}")

                # Extract response ID
                response_id = event.data.id

                # Handle different event types
                if event.type == "response.completed":
                    logger.info(f"✅ Response completed for {response_id}")

                    # Retrieve the full response data
                    try:
                        response = client.responses.retrieve(response_id)

                        # Process and store the response
                        await process_completed_response_async(response_id, response)

                    except Exception as e:
                        logger.error(f"Error retrieving response {response_id}: {e}")
                        await webhook_manager.handle_webhook(
                            response_id, "response.error", {"error": str(e)}
                        )

                elif event.type == "response.failed":
                    logger.error(f"❌ Response FAILED for {response_id}")
                    await webhook_manager.handle_webhook(
                        response_id, "response.failed", event.data
                    )

                elif event.type == "response.cancelled":
                    logger.warning(f"⏹️ Response CANCELLED for {response_id}")
                    await webhook_manager.handle_webhook(
                        response_id, "response.cancelled", event.data
                    )

                elif event.type == "response.incomplete":
                    logger.warning(f"⚠️ Response INCOMPLETE for {response_id}")
                    await webhook_manager.handle_webhook(
                        response_id, "response.incomplete", event.data
                    )

                else:
                    logger.warning(f"⚠️ Unhandled event type: {event.type}")

                return Response(status_code=200)

            except InvalidWebhookSignatureError as e:
                logger.error(f"❌ Invalid webhook signature: {e}")
                return Response("Invalid signature", status_code=400)

        else:
            # No signature verification (less secure, for testing)
            logger.warning(
                "⚠️ No OPENAI_WEBHOOK_SECRET configured, skipping signature verification"
            )

            # Parse JSON directly
            data = await request.json()
            event_type = data.get("type", "unknown")
            logger.info(f"📥 Event type: {event_type}")

            # Extract response ID
            response_id = None
            if "data" in data and isinstance(data["data"], dict):
                response_id = data["data"].get("id")

            if response_id:
                await webhook_manager.handle_webhook(
                    response_id, event_type, data.get("data")
                )

            return Response(status_code=200)

    except Exception as e:
        logger.error(f"❌ Webhook processing error: {e}")
        import traceback

        traceback.print_exc()
        return Response(status_code=500)


async def process_completed_response_async(response_id: str, response: Any) -> None:
    """Process a completed OpenAI response."""
    try:
        # Extract the final output text
        if not response.output:
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

        # Extract research steps (reasoning and web searches)
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

        # Store the processed response
        await webhook_manager.handle_webhook(
            response_id,
            "response.completed",
            {
                "raw_output": output_text,
                "citations": citations,
                "research_steps": research_steps,
                "status": "completed",
            },
        )

    except Exception as e:
        logger.error(f"Error processing completed response: {e}")
        await webhook_manager.handle_webhook(
            response_id, "response.error", {"error": str(e), "status": "error"}
        )


def main():
    """Main entry point for the webhook server."""
    parser = argparse.ArgumentParser(
        description="OpenAI Webhook Server for Diligence Agents"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to run the webhook server on (default: 8080)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--no-ngrok",
        action="store_true",
        help="Disable automatic ngrok tunnel creation",
    )
    parser.add_argument(
        "--ngrok-domain",
        type=str,
        default=None,
        help="Use a specific ngrok domain (for paid accounts)",
    )

    args = parser.parse_args()

    # Check environment
    webhook_secret = os.getenv("OPENAI_WEBHOOK_SECRET")
    ngrok_auth_token = os.getenv("NGROK_AUTH_TOKEN")
    ngrok_domain_env = os.getenv("NGROK_DOMAIN")

    print("\n" + "=" * 60)
    print("🚀 OpenAI Webhook Server Starting")
    print("=" * 60)
    print(f"📡 Server will run on: http://{args.host}:{args.port}")
    print(f"📡 Local webhook endpoint: http://{args.host}:{args.port}/webhooks/openai")

    # Set up ngrok tunnel if not disabled
    public_url = None
    if not args.no_ngrok:
        try:
            # Configure ngrok with auth token if available
            if ngrok_auth_token:
                conf.get_default().auth_token = ngrok_auth_token
                print(f"✅ Ngrok auth token configured")
            else:
                # Try to use token from ngrok config file
                ngrok_config_path = os.path.expanduser("~/.config/ngrok/ngrok.yml")
                if os.path.exists(ngrok_config_path):
                    print(f"📝 Using ngrok config from: {ngrok_config_path}")

            # Determine which domain to use
            ngrok_domain = args.ngrok_domain or ngrok_domain_env

            # Create ngrok tunnel
            print(f"🔄 Creating ngrok tunnel to port {args.port}...")

            if ngrok_domain:
                # Use custom domain (for paid accounts)
                print(f"📡 Using ngrok domain: {ngrok_domain}")
                tunnel = ngrok.connect(args.port, "http", hostname=ngrok_domain)
            else:
                # Use random ngrok subdomain
                tunnel = ngrok.connect(args.port, "http")

            public_url = tunnel.public_url

            # Update webhook URL in environment
            webhook_url = f"{public_url}/webhooks/openai"
            os.environ["OPENAI_WEBHOOK_URL"] = webhook_url

            print(f"✅ Ngrok tunnel created!")
            print(f"🌐 Public URL: {public_url}")
            print(f"🪝 Webhook URL: {webhook_url}")
            print(f"\n⚠️  IMPORTANT: Update your .env file with:")
            print(f'   OPENAI_WEBHOOK_URL="{webhook_url}"')

            # Write the webhook URL to a file for other processes to read
            webhook_url_file = os.path.join(os.path.dirname(__file__), ".webhook_url")
            with open(webhook_url_file, "w") as f:
                f.write(webhook_url)
            print(f"📄 Webhook URL saved to: {webhook_url_file}")

        except Exception as e:
            print(f"⚠️  Failed to create ngrok tunnel: {e}")
            print("   Run with --no-ngrok to disable automatic tunnel creation")
            print("   Or manually run: ngrok http 8080")
    else:
        webhook_url = os.getenv("OPENAI_WEBHOOK_URL")
        if webhook_url:
            print(f"✅ Using configured webhook URL: {webhook_url}")
        else:
            print("⚠️  OPENAI_WEBHOOK_URL not configured and ngrok disabled")
            print("   Webhooks will only work if manually configured")

    if webhook_secret:
        print("✅ Webhook secret configured for signature verification")
    else:
        print("⚠️  OPENAI_WEBHOOK_SECRET not configured")
        print("   Webhook signatures will not be verified")

    print("=" * 60 + "\n")

    # Run the server
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        # Clean up ngrok tunnel on exit
        if not args.no_ngrok and public_url:
            ngrok.disconnect(public_url)
            print("\n🛑 Ngrok tunnel closed")


if __name__ == "__main__":
    main()
