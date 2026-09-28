"""
FastAPI 流式接口 Demo

TO run:
    python -m uvicorn main:app --reload
然后打开 http://127.0.0.1:8000
"""

import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse

app = FastAPI(title="Streaming API Demo")

@app.get("/stream/chat")
async def stream_chat(q: str = "流式接口的本质"):
    async def token_stream():
        answer = (
            f"你问的是「{q}」。流式接口的本质是：服务端不把结果一次性算完再回，"
            "而是一边算一边推，客户端边收边渲染。它特别适合大模型输出，"
            "因为达默芯边思考边输出"
        )
        for token in answer:
            await asyncio.sleep(0.05)
            yield json.dumps({"delta": token}, ensure_ascii=False) + "\n"
        yield json.dumps({"done": True}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        token_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# 网页前端

@app.get("/")
async def index():
    return FileResponse("index.html")
