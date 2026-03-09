import asyncio
import json
import logging

import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_orchestrator")

async def handler(websocket):
    logger.info("New connection from %s", websocket.remote_address)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                logger.info("Received: %s", msg_type)

                if msg_type == "agent.register":
                    agent_id = data.get("agent_id")
                    logger.info("Agent registered: %s", agent_id)

                    # Send a test task
                    await asyncio.sleep(1)
                    test_task = {
                        "type": "task.assigned",
                        "task_id": "TASK-TEST-001",
                        "payload": {"command": "echo 'Hello from Forge v3 Worker'"}
                    }
                    logger.info("Assigning task: %s", test_task)
                    await websocket.send(json.dumps(test_task))

                elif msg_type == "task.started":
                    logger.info("Task %s started", data.get("task_id"))

                elif msg_type == "task.completed":
                    logger.info("Task %s completed: %s", data.get("task_id"), data.get("result"))
                    # Stop the test after one successful task
                    # await asyncio.sleep(1)
                    # logger.info("Test complete, closing connection")
                    # break

                elif msg_type == "task.failed":
                    logger.error("Task %s failed: %s", data.get("task_id"), data.get("error"))

                elif msg_type == "heartbeat":
                    logger.info("Heartbeat received from %s", data.get("agent_id", "unknown"))
                    await websocket.send(json.dumps({"type": "heartbeat.ack"}))

                else:
                    logger.warning("Unknown message type: %s", msg_type)

            except json.JSONDecodeError:
                logger.error("Received malformed JSON: %r", message)
            except Exception as e:
                logger.error("Error in handler: %s", e, exc_info=True)

    except websockets.exceptions.ConnectionClosed:
        logger.info("Connection closed by client")
    except Exception as e:
        logger.error("Connection error: %s", e)

async def main():
    async with websockets.serve(handler, "localhost", 8080):
        logger.info("Mock orchestrator running on ws://localhost:8080")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Mock orchestrator stopped by user")
