# app/providers/notion_provider.py
import json
import time
import logging
import uuid
import re
import cloudscraper
import requests
from http.cookies import SimpleCookie
from typing import Dict, Any, AsyncGenerator, List, Optional, Tuple
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.providers.base_provider import BaseProvider
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, create_chat_completion_response, DONE_CHUNK

# 设置日志记录器
logger = logging.getLogger(__name__)

class NotionAIProvider(BaseProvider):
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.api_endpoints = {
            "runInference": "https://www.notion.so/api/v3/runInferenceTranscript",
            "syncRecordValues": "https://www.notion.so/api/v3/syncRecordValuesSpaceInitial",
        }

        if not all([settings.NOTION_COOKIE, settings.NOTION_SPACE_ID, settings.NOTION_USER_ID]):
            raise ValueError("配置错误: NOTION_COOKIE, NOTION_SPACE_ID 和 NOTION_USER_ID 必须在 .env 文件中全部设置。")

        self._warmup_session()

    def _warmup_session(self, scraper: Optional[Any] = None):
        try:
            logger.info("正在进行会话预热 (Session Warm-up)...")
            headers = self._prepare_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )
            target_scraper = scraper or self.scraper
            response = target_scraper.get(
                "https://www.notion.so/",
                headers=headers,
                timeout=30,
                proxies=self._prepare_proxies(),
            )
            response.raise_for_status()
            logger.info("会话预热成功。")
        except Exception as e:
            logger.error(f"会话预热失败: {e}", exc_info=True)

    def _prepare_proxies(self) -> Optional[Dict[str, str]]:
        proxy = (settings.NOTION_PROXY or "").strip()
        if not proxy:
            return None

        if "://" not in proxy:
            proxy = f"http://{proxy}"
        return {"http": proxy, "https": proxy}

    def _format_response_error(self, response: Optional[requests.Response]) -> str:
        if response is None:
            return ""

        body = response.text[:500] if response.text else ""
        return f"status={response.status_code}, body={body}"

    def _notion_error_detail(self, response: Optional[requests.Response]) -> str:
        if response is None or not response.text:
            return ""

        try:
            data = response.json()
        except ValueError:
            return ""

        parts = []
        name = data.get("name")
        if isinstance(name, str) and name:
            parts.append(name)

        client_data = data.get("clientData")
        if isinstance(client_data, dict):
            error_type = client_data.get("type")
            if isinstance(error_type, str) and error_type:
                parts.append(error_type)

        debug_message = data.get("debugMessage")
        if isinstance(debug_message, str) and debug_message:
            parts.append(debug_message)

        return " / ".join(parts)

    def _post_json_with_retry(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout: int,
        action: str,
        stream: bool = False,
        attempts: int = 2,
    ) -> requests.Response:
        last_error: Optional[Exception] = None
        session = requests.Session()
        headers = dict(headers)
        cookie_header = headers.pop("Cookie", "")
        self._load_cookies_into_session(session, cookie_header)
        proxies = self._prepare_proxies()
        if proxies:
            session.proxies.update(proxies)

        for attempt in range(1, attempts + 1):
            try:
                response = session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                    stream=stream
                )
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as e:
                response = e.response
                status_code = response.status_code if response is not None else None
                detail = self._format_response_error(response)
                logger.error(f"{action}失败: {detail}", exc_info=True)

                if status_code in (401, 403):
                    notion_detail = self._notion_error_detail(response)
                    suffix = f" ({notion_detail})" if notion_detail else ""
                    raise HTTPException(status_code=502, detail=f"{action}失败：Notion 凭证无效或无权限{suffix}。")
                if status_code is not None and status_code < 500:
                    raise HTTPException(status_code=502, detail=f"{action}失败：Notion 返回 {status_code}。")

                last_error = e
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(f"{action}第 {attempt}/{attempts} 次请求失败: {e}", exc_info=True)

            if attempt < attempts:
                session.close()
                session = requests.Session()
                self._load_cookies_into_session(session, cookie_header)
                if proxies:
                    session.proxies.update(proxies)

        raise HTTPException(status_code=502, detail=f"{action}失败：Notion 上游连接异常: {last_error}")

    def _load_cookies_into_session(self, session: requests.Session, cookie_header: str):
        if not cookie_header:
            return

        parsed = SimpleCookie()
        parsed.load(cookie_header)
        for name, morsel in parsed.items():
            session.cookies.set(name, morsel.value, domain=".notion.so", path="/")

    async def chat_completion(self, request_data: Dict[str, Any]):
        stream = request_data.get("stream", True)
        request_id = f"chatcmpl-{uuid.uuid4()}"

        async def collect_response() -> Tuple[str, str]:
            model_name = request_data.get("model", settings.DEFAULT_MODEL)
            mapped_model = settings.MODEL_MAP.get(model_name, settings.MODEL_MAP.get(settings.DEFAULT_MODEL, "oatmeal-cookie"))
            incremental_fragments: List[str] = []
            final_message: Optional[str] = None

            thread_type = "markdown-chat" if mapped_model.startswith("vertex-") else "workflow"

            thread_id = str(uuid.uuid4())
            payload = self._prepare_payload(request_data, thread_id, mapped_model, thread_type)
            headers = self._prepare_headers(accept="application/x-ndjson")

            def sync_stream_iterator():
                try:
                    logger.info(f"请求 Notion AI URL: {self.api_endpoints['runInference']}")
                    logger.info(f"请求体: {json.dumps(payload, indent=2, ensure_ascii=False)}")

                    response = self._post_json_with_retry(
                        self.api_endpoints['runInference'],
                        headers=headers,
                        payload=payload,
                        stream=True,
                        timeout=settings.API_REQUEST_TIMEOUT,
                        action="请求 Notion AI",
                    )
                    for line in response.iter_lines():
                        if line:
                            yield line
                except Exception as e:
                    yield e

            sync_gen = sync_stream_iterator()

            while True:
                line = await run_in_threadpool(lambda: next(sync_gen, None))
                if line is None:
                    break
                if isinstance(line, Exception):
                    raise line

                parsed_results = self._parse_ndjson_line_to_texts(line)
                for text_type, content in parsed_results:
                    if text_type == 'final':
                        final_message = content
                    elif text_type == 'incremental':
                        incremental_fragments.append(content)

            full_response = ""
            if final_message:
                full_response = final_message
                logger.info(f"成功从 record-map 或 Gemini patch/event 中提取到最终消息。")
            else:
                full_response = "".join(incremental_fragments)
                logger.info(f"使用拼接所有增量片段的方式获得最终消息。")

            if not full_response:
                polled_response = await self._poll_final_response(thread_id)
                if polled_response:
                    return model_name, self._clean_content(polled_response)

                logger.warning("警告: Notion 返回的数据流和轮询结果中均未提取到任何有效文本。请检查您的 .env 配置是否全部正确且凭证有效。")
                return model_name, ""

            cleaned_response = self._clean_content(full_response)
            logger.info(f"清洗后的最终响应: {cleaned_response}")
            return model_name, cleaned_response

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            try:
                model_name = request_data.get("model", settings.DEFAULT_MODEL)
                role_chunk = create_chat_completion_chunk(request_id, model_name, role="assistant")
                yield create_sse_data(role_chunk)
                model_name, cleaned_response = await collect_response()
                if cleaned_response:
                    chunk = create_chat_completion_chunk(request_id, model_name, content=cleaned_response)
                    yield create_sse_data(chunk)

                final_chunk = create_chat_completion_chunk(request_id, model_name, finish_reason="stop")
                yield create_sse_data(final_chunk)
                yield DONE_CHUNK

            except Exception as e:
                error_message = f"处理 Notion AI 流时发生意外错误: {str(e)}"
                logger.error(error_message, exc_info=True)
                error_chunk = {"error": {"message": error_message, "type": "internal_server_error"}}
                yield create_sse_data(error_chunk)
                yield DONE_CHUNK

        if stream:
            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        model_name, cleaned_response = await collect_response()
        return JSONResponse(content=create_chat_completion_response(request_id, model_name, cleaned_response))

    async def _poll_final_response(self, thread_id: str) -> str:
        last_message_ids: List[str] = []
        for _ in range(20):
            await run_in_threadpool(lambda: time.sleep(1.5))
            try:
                thread_data = await self._sync_thread(thread_id)
                message_ids = self._message_ids_from_thread_record(thread_data, thread_id)
                if not message_ids:
                    continue

                last_message_ids = message_ids
                message_data = await self._sync_thread_messages(thread_id, message_ids)
                content = self._extract_agent_text_from_record_map(message_data.get("recordMap", {}), message_ids)
                if content:
                    return content
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"轮询 Notion 最终答案失败: {e}", exc_info=True)

        logger.warning(f"轮询 Notion 最终答案超时, thread_id={thread_id}, last_message_ids={last_message_ids}")
        return ""

    async def _sync_thread(self, thread_id: str) -> Dict[str, Any]:
        payload = {
            "requests": [{
                "pointer": {
                    "table": "thread",
                    "id": thread_id,
                    "spaceId": settings.NOTION_SPACE_ID,
                },
                "version": -1,
            }]
        }
        response = await run_in_threadpool(
            lambda: self._post_json_with_retry(
                self.api_endpoints["syncRecordValues"],
                headers=self._prepare_headers(accept="application/json"),
                payload=payload,
                timeout=20,
                action="同步 Notion 线程",
            )
        )
        return response.json()

    async def _sync_thread_messages(self, thread_id: str, message_ids: List[str]) -> Dict[str, Any]:
        payload = {
            "requests": [
                {
                    "pointer": {
                        "table": "thread_message",
                        "id": message_id,
                        "spaceId": settings.NOTION_SPACE_ID,
                    },
                    "version": -1,
                }
                for message_id in message_ids
            ]
        }
        response = await run_in_threadpool(
            lambda: self._post_json_with_retry(
                self.api_endpoints["syncRecordValues"],
                headers=self._prepare_headers(accept="application/json"),
                payload=payload,
                timeout=20,
                action="同步 Notion 线程消息",
            )
        )
        return response.json()

    def _message_ids_from_thread_record(self, thread_data: Dict[str, Any], thread_id: str) -> List[str]:
        record_map = thread_data.get("recordMap", {})
        thread_record = record_map.get("thread", {}).get(thread_id, {})
        value = thread_record.get("value", {}).get("value", {})
        messages = value.get("messages", [])
        return [message_id for message_id in messages if isinstance(message_id, str) and message_id.strip()]

    def _extract_agent_text_from_record_map(self, record_map: Dict[str, Any], message_ids: List[str]) -> str:
        thread_messages = record_map.get("thread_message", {})
        for message_id in reversed(message_ids):
            message = thread_messages.get(message_id, {})
            step = message.get("value", {}).get("value", {}).get("step", {})
            if step.get("type") != "agent-inference":
                continue

            data = message.get("value", {}).get("value", {}).get("data", {})
            if data and not data.get("completed", False):
                continue

            content = self._extract_agent_step_text(step.get("value"))
            if content:
                return content
        return ""

    def _extract_agent_step_text(self, value: Any) -> str:
        if isinstance(value, list):
            parts = []
            for item in value:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                content = item.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
            return "".join(parts).strip()

        if isinstance(value, dict):
            nested_value = value.get("value")
            if isinstance(nested_value, list):
                return self._extract_agent_step_text(nested_value)
            content = value.get("content")
            if isinstance(content, str):
                return content.strip()

        if isinstance(value, str):
            return value.strip()

        return ""

    def _prepare_headers(self, accept: Optional[str] = "application/x-ndjson") -> Dict[str, str]:
        cookie_source = (settings.NOTION_COOKIE or "").strip()
        cookie_header = cookie_source if "=" in cookie_source else f"token_v2={cookie_source}"

        headers = {
            "Content-Type": "application/json",
            "Cookie": cookie_header,
            "x-notion-space-id": settings.NOTION_SPACE_ID,
            "x-notion-active-user-header": settings.NOTION_USER_ID,
            "notion-client-version": settings.NOTION_CLIENT_VERSION,
            "x-notion-client-version": settings.NOTION_CLIENT_VERSION,
            "notion-audit-log-platform": "web",
            "Origin": "https://www.notion.so",
            "Referer": settings.NOTION_REFERER or "https://www.notion.so/",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,fr;q=0.6",
            "Connection": "close",
            "priority": "u=1, i",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        }
        if accept:
            headers["Accept"] = accept
        return headers

    def _normalize_block_id(self, block_id: str) -> str:
        if not block_id: return block_id
        b = block_id.replace("-", "").strip()
        if len(b) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", b):
            return f"{b[0:8]}-{b[8:12]}-{b[12:16]}-{b[16:20]}-{b[20:]}"
        return block_id

    def _prepare_payload(self, request_data: Dict[str, Any], thread_id: str, mapped_model: str, thread_type: str) -> Dict[str, Any]:
        req_block_id = request_data.get("notion_block_id") or settings.NOTION_BLOCK_ID
        normalized_block_id = self._normalize_block_id(req_block_id) if req_block_id else None

        context_value: Dict[str, Any] = {
            "timezone": "Asia/Shanghai",
            "spaceId": settings.NOTION_SPACE_ID,
            "userId": settings.NOTION_USER_ID,
            "userEmail": settings.NOTION_USER_EMAIL,
            "currentDatetime": datetime.now().astimezone().isoformat(),
        }
        if normalized_block_id:
            context_value["blockId"] = normalized_block_id

        config_value: Dict[str, Any]

        if mapped_model.startswith("vertex-"):
            logger.info(f"检测到 Gemini 模型 ({mapped_model})，应用特定的 config 和 context。")
            context_value.update({
                "userName": f" {settings.NOTION_USER_NAME}",
                "spaceName": f"{settings.NOTION_USER_NAME}的 Notion",
                "spaceViewId": "2008eefa-d0dc-80d5-9e67-000623befd8f",
                "surface": "ai_module"
            })
            config_value = {
                "type": thread_type,
                "model": mapped_model,
                "useWebSearch": True,
                "enableAgentAutomations": False, "enableAgentIntegrations": False,
                "enableBackgroundAgents": False, "enableCodegenIntegration": False,
                "enableCustomAgents": False, "enableExperimentalIntegrations": False,
                "enableLinkedDatabases": False, "enableAgentViewVersionHistoryTool": False,
                "searchScopes": [{"type": "everything"}], "enableDatabaseAgents": False,
                "enableAgentComments": False, "enableAgentForms": False,
                "enableAgentMakesFormulas": False, "enableUserSessionContext": False,
                "modelFromUser": True, "isCustomAgent": False
            }
        else:
            context_value.update({
                "userName": settings.NOTION_USER_NAME,
                "spaceName": settings.NOTION_SPACE_NAME or settings.NOTION_USER_NAME,
                "surface": "workflows"
            })
            if settings.NOTION_SPACE_VIEW_ID:
                context_value["spaceViewId"] = settings.NOTION_SPACE_VIEW_ID

            config_value = {
                "type": thread_type,
                "enableAgentAutomations": True,
                "enableAgentIntegrations": True,
                "enableCustomAgents": True,
                "enableExperimentalIntegrations": False,
                "enableAgentDiffs": True,
                "enableAgentUpdatePagePatch": True,
                "enableCsvAttachmentSupport": True,
                "enableDatabaseAgents": True,
                "showDatabaseAgentsDiscoverability": False,
                "enableAgentThreadTools": False,
                "enableCrdtOperations": False,
                "enableAgentCardCustomization": True,
                "enableSystemPromptAsPage": False,
                "enableUserSessionContext": False,
                "enableLargeToolResultComputerOffload": False,
                "enableScriptAgentAdvanced": False,
                "enableScriptAgent": True,
                "enableScriptAgentSearchConnectorsInCustomAgent": False,
                "enableScriptAgentGoogleDriveInCustomAgent": False,
                "enableScriptAgentGoogleDriveOAuthInCustomAgent": False,
                "enableScriptAgentSlack": True,
                "enableScriptAgentMcpServers": False,
                "enableScriptAgentGtm": False,
                "enableScriptAgentCustomToolCalling": True,
                "enableComputer": False,
                "enableCreateAndRunThread": True,
                "enableSoftwareFactoryPage": False,
                "enableAgentGenerateImage": False,
                "enableSpeculativeSearch": False,
                "enableQueryCalendar": False,
                "enableQueryMail": False,
                "enableMailExplicitToolCalls": True,
                "enableMailNotificationPreferences": False,
                "enableMailAgentMultiProviderSupport": False,
                "useRulePrioritization": True,
                "availableConnectors": [],
                "customConnectorInfo": [],
                "searchScopes": [{"type": "everything"}],
                "useSearchToolV2": False,
                "useWebSearch": True,
                "isHipaa": False,
                "yoloMode": False,
                "useReadOnlyMode": False,
                "writerMode": False,
                "modelFromUser": False,
                "isCustomAgent": False,
                "isCustomAgentBuilder": False,
                "isAgentResearchRequest": False,
                "useCustomAgentDraft": False,
                "use_draft_actor_pointer": False,
                "enableUpdatePageAutofixer": True,
                "enableMarkdownVNext": False,
                "updatePageStaleViewGuardEnabled": False,
                "enableUpdatePageOrderUpdates": True,
                "enableAgentSupportPropertyReorder": True,
                "agentShortUpdatePageResult": True,
                "enableAgentAskSurvey": True,
                "databaseAgentConfigMode": False,
                "isOnboardingAgent": False,
                "isMobile": False,
                "useContextualCoreDocsAutoLoad": False,
                "useDocPreviewsForCoreAutoLoad": False,
                "isThreadStartedByAdmin": True,
            }
            if mapped_model:
                config_value["model"] = mapped_model

        transcript = [
            {"id": str(uuid.uuid4()), "type": "config", "value": config_value},
            {"id": str(uuid.uuid4()), "type": "context", "value": context_value}
        ]

        for msg in request_data.get("messages", []):
            if msg.get("role") == "user":
                transcript.append({
                    "id": str(uuid.uuid4()),
                    "type": "user",
                    "value": [[msg.get("content")]],
                    "userId": settings.NOTION_USER_ID,
                    "createdAt": datetime.now().astimezone().isoformat()
                })
            elif msg.get("role") == "assistant":
                transcript.append({"id": str(uuid.uuid4()), "type": "agent-inference", "value": [{"type": "text", "content": msg.get("content")}]})

        payload = {
            "traceId": str(uuid.uuid4()),
            "spaceId": settings.NOTION_SPACE_ID,
            "transcript": transcript,
            "threadId": thread_id,
            "createThread": True,
            "isPartialTranscript": False,
            "asPatchResponse": True,
            "generateTitle": True,
            "saveAllThreadOperations": True,
            "threadType": thread_type,
            "setUnreadState": True,
            "createdSource": context_value.get("surface", "workflows"),
            "isUserInAnySalesAssistedSpace": False,
            "isSpaceSalesAssisted": False,
            "threadParentPointer": {
                "table": "space",
                "id": settings.NOTION_SPACE_ID,
                "spaceId": settings.NOTION_SPACE_ID,
            },
            "debugOverrides": {
                "emitAgentSearchExtractedResults": True,
                "cachedInferences": {},
                "annotationInferences": {},
                "emitInferences": False
            },
        }

        if mapped_model.startswith("vertex-"):
            logger.info("为 Gemini 请求添加 debugOverrides。")

        return payload

    def _clean_content(self, content: str) -> str:
        if not content:
            return ""

        content = re.sub(r'<lang primary="[^"]*"\s*/>\n*', '', content)
        content = re.sub(r'<thinking>[\s\S]*?</thinking>\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'<thought>[\s\S]*?</thought>\s*', '', content, flags=re.IGNORECASE)

        content = re.sub(r'^.*?Chinese whatmodel I am.*?Theyspecifically.*?requested.*?me.*?to.*?reply.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?This.*?is.*?a.*?straightforward.*?question.*?about.*?my.*?identity.*?asan.*?AI.*?assistant\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?Idon\'t.*?need.*?to.*?use.*?any.*?tools.*?for.*?this.*?-\s*it\'s.*?asimple.*?informational.*?response.*?aboutwhat.*?I.*?am\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?Sincethe.*?user.*?asked.*?in.*?Chinese.*?and.*?specifically.*?requested.*?a.*?Chinese.*?response.*?I.*?should.*?respond.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?What model are you.*?in Chinese and specifically requesting.*?me.*?to.*?reply.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?This.*?is.*?a.*?question.*?about.*?my.*?identity.*?not requiring.*?any.*?tool.*?use.*?I.*?should.*?respond.*?directly.*?to.*?the.*?user.*?in.*?Chinese.*?as.*?requested\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?I.*?should.*?identify.*?myself.*?as.*?Notion.*?AI.*?as.*?mentioned.*?in.*?the.*?system.*?prompt.*?\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'^.*?I.*?should.*?not.*?make.*?specific.*?claims.*?about.*?the.*?underlying.*?model.*?architecture.*?since.*?that.*?information.*?is.*?not.*?provided.*?in.*?my.*?context\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)

        return content.strip()

    def _parse_ndjson_line_to_texts(self, line: bytes) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        try:
            s = line.decode("utf-8", errors="ignore").strip()
            if not s: return results

            data = json.loads(s)
            logger.debug(f"原始响应数据: {json.dumps(data, ensure_ascii=False)}")

            # 格式1: Gemini 返回的 markdown-chat 事件
            if data.get("type") == "markdown-chat":
                content = data.get("value", "")
                if content:
                    logger.info("从 'markdown-chat' 直接事件中提取到内容。")
                    results.append(('final', content))

            # 格式2: Claude 和 GPT 返回的补丁流，以及 Gemini 的 patch 格式
            elif data.get("type") == "patch" and "v" in data:
                for operation in data.get("v", []):
                    if not isinstance(operation, dict): continue

                    op_type = operation.get("o")
                    path = operation.get("p", "")
                    value = operation.get("v")

                    # 【修改】Gemini 的完整内容 patch 格式
                    if op_type == "a" and path.endswith("/s/-") and isinstance(value, dict) and value.get("type") == "markdown-chat":
                        content = value.get("value", "")
                        if content:
                            logger.info("从 'patch' (Gemini-style) 中提取到完整内容。")
                            results.append(('final', content))

                    # 【修改】Gemini 的增量内容 patch 格式
                    elif op_type == "x" and "/s/" in path and path.endswith("/value") and isinstance(value, str):
                        content = value
                        if content:
                            logger.info(f"从 'patch' (Gemini增量) 中提取到内容: {content}")
                            results.append(('incremental', content))

                    # 【修改】Claude 和 GPT 的增量内容 patch 格式
                    elif op_type == "x" and "/value/" in path and isinstance(value, str):
                        content = value
                        if content:
                            logger.info(f"从 'patch' (Claude/GPT增量) 中提取到内容: {content}")
                            results.append(('incremental', content))

                    # 【修改】Claude 和 GPT 的完整内容 patch 格式
                    elif op_type == "a" and path.endswith("/value/-") and isinstance(value, dict) and value.get("type") == "text":
                        content = value.get("content", "")
                        if content:
                            logger.info("从 'patch' (Claude/GPT-style) 中提取到完整内容。")
                            results.append(('final', content))

            # 格式3: 处理record-map类型的数据
            elif data.get("type") == "record-map" and "recordMap" in data:
                record_map = data["recordMap"]
                if "thread_message" in record_map:
                    for msg_id, msg_data in record_map["thread_message"].items():
                        value_data = msg_data.get("value", {}).get("value", {})
                        step = value_data.get("step", {})
                        if not step: continue

                        content = ""
                        step_type = step.get("type")

                        if step_type == "markdown-chat":
                            content = step.get("value", "")
                        elif step_type == "agent-inference":
                            agent_values = step.get("value", [])
                            if isinstance(agent_values, list):
                                for item in agent_values:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        content = item.get("content", "")
                                        break

                        if content and isinstance(content, str):
                            logger.info(f"从 record-map (type: {step_type}) 提取到最终内容。")
                            results.append(('final', content))
                            break

        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"解析NDJSON行失败: {e} - Line: {line.decode('utf-8', errors='ignore')}")

        return results

    async def get_models(self) -> JSONResponse:
        model_data = {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": int(time.time()), "owned_by": "lzA6"}
                for name in settings.KNOWN_MODELS
            ]
        }
        return JSONResponse(content=model_data)
