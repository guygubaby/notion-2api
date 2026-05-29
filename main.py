# main.py
import logging
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.providers.notion_provider import NotionAIProvider

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

provider = NotionAIProvider()
_response_store: Dict[str, Dict[str, Any]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"应用启动中... {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("服务已配置为 Notion AI 代理模式。")
    logger.info(f"服务将在 http://localhost:{settings.NGINX_PORT} 上可用")
    yield
    logger.info("应用关闭。")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = time.time()
    response = await call_next(request)
    elapsed_ms = int((time.time() - started_at) * 1000)
    logger.info(
        "HTTP %s %s -> %s (%sms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

async def verify_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key")
):
    if settings.API_MASTER_KEY and settings.API_MASTER_KEY != "1":
        token = x_api_key
        if not token and authorization and "bearer" in authorization.lower():
            token = authorization.split(" ")[-1]
        if not token:
            raise HTTPException(status_code=401, detail="需要 Bearer Token 或 x-api-key 认证。")
        if token != settings.API_MASTER_KEY:
            raise HTTPException(status_code=403, detail="无效的 API Key。")

def _extract_openai_text(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            text = _extract_openai_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)

    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), (str, list, dict)):
            return _extract_openai_text(value["content"])
        if isinstance(value.get("input"), (str, list, dict)):
            return _extract_openai_text(value["input"])

    return ""

def _responses_to_chat_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    messages: List[Dict[str, str]] = []
    instructions = _extract_openai_text(request_data.get("instructions"))
    if instructions:
        messages.append({"role": "user", "content": f"System instruction:\n{instructions}"})

    input_data = request_data.get("input", request_data.get("messages", ""))
    if isinstance(input_data, str):
        if input_data:
            messages.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        for item in input_data:
            if isinstance(item, dict):
                role = item.get("role", "user")
                content = _extract_openai_text(item.get("content", item))
                if not content:
                    continue
                messages.append({
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content,
                })
            else:
                content = _extract_openai_text(item)
                if content:
                    messages.append({"role": "user", "content": content})
    else:
        content = _extract_openai_text(input_data)
        if content:
            messages.append({"role": "user", "content": content})

    if not messages:
        messages.append({"role": "user", "content": ""})

    return {
        "model": request_data.get("model", settings.DEFAULT_MODEL),
        "stream": False,
        "messages": messages,
    }

def _responses_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    usage = usage or {}
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

def _chat_usage(usage: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    usage = usage or {}
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

def _openai_error_payload(message: str, error_type: str = "api_error") -> Dict[str, Any]:
    usage = _chat_usage()
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        },
        "usage": usage,
    }

def _model_data(model_id: str) -> Dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "notion",
        "usage": _chat_usage(),
    }

def _sse_data(data: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode("utf-8")

def _create_completion_object(request_id: str, model: str, text: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": request_id,
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "text": text,
                "index": 0,
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": _chat_usage(usage),
    }

def _create_response_object(response_id: str, model: str, content: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    message_id = f"msg_{uuid.uuid4().hex}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": [
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": content,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": True,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
    }

def _responses_sse_event(event: str, data: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    error_type = "invalid_request_error" if exc.status_code == 404 else "api_error"
    return JSONResponse(
        status_code=exc.status_code,
        content=_openai_error_payload(str(exc.detail), error_type),
    )

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_openai_error_payload(str(exc), "invalid_request_error"),
    )

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
@app.post("/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request) -> StreamingResponse:
    try:
        request_data = await request.json()
        return await provider.chat_completion(request_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理聊天请求时发生顶层错误: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=_openai_error_payload(f"内部服务器错误: {str(e)}"),
        )

@app.post("/v1/completions", dependencies=[Depends(verify_api_key)])
@app.post("/completions", dependencies=[Depends(verify_api_key)])
async def completions(request: Request):
    try:
        request_data = await request.json()
        prompt = request_data.get("prompt", request_data.get("input", ""))
        prompt_text = _extract_openai_text(prompt)
        if not prompt_text and prompt not in ("", None):
            prompt_text = json.dumps(prompt, ensure_ascii=False)

        model = request_data.get("model", settings.DEFAULT_MODEL)
        chat_request = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        response = await provider.chat_completion(chat_request)
        chat_data = json.loads(response.body.decode("utf-8"))
        content = chat_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = _chat_usage(chat_data.get("usage"))
        completion_id = f"cmpl-{uuid.uuid4().hex}"

        if request_data.get("stream"):
            async def stream_generator():
                if content:
                    yield _sse_data({
                        "id": completion_id,
                        "object": "text_completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {"text": content, "index": 0, "logprobs": None, "finish_reason": None}
                        ],
                        "usage": usage,
                    })
                yield _sse_data({
                    "id": completion_id,
                    "object": "text_completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {"text": "", "index": 0, "logprobs": None, "finish_reason": "stop"}
                    ],
                    "usage": usage,
                })
                yield _sse_data({
                    "id": completion_id,
                    "object": "text_completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [],
                    "usage": usage,
                })
                yield b"data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        return JSONResponse(content=_create_completion_object(completion_id, model, content, usage))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理 OpenAI Completions 请求时发生顶层错误: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=_openai_error_payload(f"内部服务器错误: {str(e)}"),
        )

@app.post("/v1/responses", dependencies=[Depends(verify_api_key)])
@app.post("/responses", dependencies=[Depends(verify_api_key)])
async def responses(request: Request):
    try:
        request_data = await request.json()
        chat_request = _responses_to_chat_request(request_data)
        response = await provider.chat_completion(chat_request)
        chat_data = json.loads(response.body.decode("utf-8"))
        content = chat_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        model = request_data.get("model", chat_data.get("model", settings.DEFAULT_MODEL))
        usage = _responses_usage(chat_data.get("usage"))
        response_id = f"resp_{uuid.uuid4().hex}"
        response_data = _create_response_object(response_id, model, content, usage)
        _response_store[response_id] = response_data

        if request_data.get("stream"):
            async def stream_generator():
                item = response_data["output"][0]
                part = item["content"][0]
                zero_usage = _responses_usage(None)
                created = {**response_data, "status": "in_progress", "output": [], "output_text": "", "usage": zero_usage}
                yield _responses_sse_event("response.created", {"type": "response.created", "response": created, "usage": zero_usage})
                yield _responses_sse_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "content": []},
                    "usage": zero_usage,
                })
                yield _responses_sse_event("response.content_part.added", {
                    "type": "response.content_part.added",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                    "usage": zero_usage,
                })
                if content:
                    yield _responses_sse_event("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": content,
                        "usage": usage,
                    })
                yield _responses_sse_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "text": content,
                    "usage": usage,
                })
                yield _responses_sse_event("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": part,
                    "usage": usage,
                })
                yield _responses_sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": item,
                    "usage": usage,
                })
                yield _responses_sse_event("response.completed", {"type": "response.completed", "response": response_data, "usage": usage})
                yield b"data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        return JSONResponse(content=response_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理 OpenAI Responses 请求时发生顶层错误: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=_openai_error_payload(f"内部服务器错误: {str(e)}"),
        )

@app.get("/v1/responses/{response_id}/input_items", dependencies=[Depends(verify_api_key)])
@app.get("/responses/{response_id}/input_items", dependencies=[Depends(verify_api_key)])
async def response_input_items(response_id: str):
    return JSONResponse(content={
        "object": "list",
        "data": [],
        "first_id": None,
        "last_id": None,
        "has_more": False,
        "usage": _responses_usage(None),
    })

@app.get("/v1/responses/{response_id}", dependencies=[Depends(verify_api_key)])
@app.get("/responses/{response_id}", dependencies=[Depends(verify_api_key)])
async def retrieve_response(response_id: str):
    response_data = _response_store.get(response_id)
    if not response_data:
        response_data = _create_response_object(response_id, settings.DEFAULT_MODEL, "", _responses_usage(None))
    return JSONResponse(content=response_data)

@app.get("/v1/models", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
@app.get("/models", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def list_models():
    return await provider.get_models()

@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
@app.get("/models/{model_id}", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def retrieve_model(model_id: str):
    return JSONResponse(content=_model_data(model_id))

@app.get("/", summary="根路径")
def root():
    return {"message": f"欢迎来到 {settings.APP_NAME} v{settings.APP_VERSION}. 服务运行正常。"}
