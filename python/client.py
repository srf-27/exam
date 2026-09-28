"""
命令行客户端：「消费」流式接口。
"""

import asyncio
import os

import httpx

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")


async def demo_ndjson():
    """消费 NDJSON：每行一个 JSON，边收边打印。"""
    print("\n=== /stream/chat ===")
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", f"{BASE}/stream/chat") as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = httpx.Response(200, content=line).json()
                if chunk.get("done"):
                    break
                print(chunk["delta"], end="", flush=True)


async def main():
    await demo_ndjson()


if __name__ == "__main__":
    asyncio.run(main())
