import json
import time
import uuid
import asyncio
import random
import re
import html
from typing import Dict, Any, Optional, List
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from playwright.async_api import async_playwright, Page, BrowserContext, Error as PlaywrightError
from loguru import logger
import httpx

from app.core.config import settings
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, DONE_CHUNK

# --- Unitate de lucru (Worker) ---
class BrowserWorker:
    """Reprezintă o fereastră incognito independentă a browserului"""
    def __init__(self, browser):
        self.browser = browser
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.uses_count = 0
        self.created_at = 0
        self.id = str(uuid.uuid4())[:8]

    async def init(self):
        """Inițializează această fereastră"""
        try:
            if self.context:
                await self.close()

            self.context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="ro-RO",
                timezone_id="Europe/Bucharest"
            )
            
            self.page = await self.context.new_page()
            await self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            
            logger.info(f"🔧 [Worker-{self.id}] Se preîncălzește...")
            
            await self.page.goto(
                "https://toolbaz.com/writer/chat-gpt-alternative", 
                wait_until="domcontentloaded", 
                timeout=60000
            )

            self.created_at = time.time()
            self.uses_count = 0
            logger.info(f"✅ [Worker-{self.id}] Pregătit")
            return True
        except Exception as e:
            logger.error(f"❌ [Worker-{self.id}] Inițializare eșuată: {e}")
            await self.close()
            return False

    async def get_token_data(self):
        """Extrage credențialele necesare pentru request-ul direct"""
        if not self.page or self.page.is_closed():
            await self.init()

        try:
            # Așteptăm ca scripturile Toolbaz să se încarce
            await self.page.wait_for_function("typeof xA1pY === 'function'", timeout=10000)
            
            result = await self.page.evaluate("""() => {
                function getCookie(name) {
                    const value = `; ${document.cookie}`;
                    const parts = value.split(`; ${name}=`);
                    if (parts.length === 2) return parts.pop().split(';').shift();
                    return null;
                }
                let sessionId = getCookie("SessionID");
                let token = typeof xA1pY === 'function' ? xA1pY() : "";
                return { sessionId, token };
            }""")
            return result
        except Exception as e:
            return {"error": str(e)}

    async def close(self):
        try:
            if self.context: await self.context.close()
        except: pass
        self.context = None
        self.page = None

# --- Furnizor Principal (Provider) ---
class ToolbazProvider:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.pool = asyncio.Queue()
        self.api_token_url = "https://data.toolbaz.com/token.php"
        self.api_writing_url = "https://data.toolbaz.com/writing.php"
        self.request_timestamps: List[float] = []
        self.rate_limit_lock = asyncio.Lock()

    async def initialize(self):
        logger.info(f"🚀 Pornire browser cluster (mărime: {settings.BROWSER_POOL_SIZE})...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        for _ in range(settings.BROWSER_POOL_SIZE):
            worker = BrowserWorker(self.browser)
            asyncio.create_task(self._init_and_push_worker(worker))
            await asyncio.sleep(2)

    async def _init_and_push_worker(self, worker: BrowserWorker):
        if await worker.init():
            await self.pool.put(worker)
        else:
            await asyncio.sleep(10)
            await self._init_and_push_worker(worker)

    def _clean_response_text(self, text: str) -> str:
        if not text: return ""
        text = text.replace("<br>", "\n").replace("<br/>", "\n")
        text = html.unescape(text)
        # Elimină prefixele inutile care încurcă Dyad
        text = re.sub(r'^\[model:.*?\]\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^Toolbaz.*?:', '', text, flags=re.IGNORECASE)
        return text.strip()

    async def chat_completion(self, request_data: Dict[str, Any]):
        model = request_data.get("model", settings.DEFAULT_MODEL)
        messages = request_data.get("messages", [])
        stream = request_data.get("stream", False)
        
        # --- FIX: PROMPT FLATTENING (PENTRU CONTEXT FIȘIERE) ---
        # Unim tot istoricul pentru ca AI-ul să vadă fișierele trimise anterior
        full_context = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                full_context += f"System Instruction: {content}\n\n"
            elif role == "user":
                full_context += f"User: {content}\n\n"
            elif role == "assistant":
                full_context += f"Assistant: {content}\n\n"
        
        full_context += "Assistant:" # Împingem AI-ul să răspundă
        
        # Padding invizibil pentru a "păcăli" filtrele simple
        formatted_text = f"\u3164 : {full_context}\u3164"

        worker: BrowserWorker = await self.pool.get()
        try:
            logger.info(f"🤖 Procesare cerere cu [Worker-{worker.id}]")
            
            security_data = await worker.get_token_data()
            if "error" in security_data:
                await worker.init()
                security_data = await worker.get_token_data()

            async with httpx.AsyncClient() as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://toolbaz.com/writer/chat-gpt-alternative",
                    "Cookie": f"SessionID={security_data['sessionId']}"
                }

                # Pasul 1: Obține Capcha Token
                t_resp = await client.post(
                    self.api_token_url,
                    data={"session_id": security_data['sessionId'], "token": security_data['token']},
                    headers=headers
                )
                capcha = t_resp.json().get("token")

                # Pasul 2: Trimite Prompt-ul complet
                chat_resp = await client.post(
                    self.api_writing_url,
                    data={
                        "text": formatted_text,
                        "capcha": capcha,
                        "model": model,
                        "session_id": security_data['sessionId']
                    },
                    headers=headers,
                    timeout=120
                )

                clean_text = self._clean_response_text(chat_resp.text)
                request_id = f"chatcmpl-{uuid.uuid4()}"

                if not stream:
                    await self.pool.put(worker)
                    return JSONResponse({
                        "id": request_id,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": clean_text}, "finish_reason": "stop"}]
                    })

                # --- STREAMING LOGIC ---
                async def stream_generator():
                    try:
                        # Trimitem un chunk inițial pentru a preveni timeout-ul Dyad
                        yield create_sse_data(create_chat_completion_chunk(request_id, model, ""))
                        
                        chunk_size = 40
                        for i in range(0, len(clean_text), chunk_size):
                            part = clean_text[i:i+chunk_size]
                            yield create_sse_data(create_chat_completion_chunk(request_id, model, part))
                            await asyncio.sleep(0.01)
                        
                        yield create_sse_data(create_chat_completion_chunk(request_id, model, "", "stop"))
                        yield DONE_CHUNK
                    finally:
                        await self.pool.put(worker)

                return StreamingResponse(stream_generator(), media_type="text/event-stream")

        except Exception as e:
            logger.error(f"❌ Eroare Worker-{worker.id}: {e}")
            await self.pool.put(worker)
            raise HTTPException(status_code=500, detail=str(e))

    async def get_models(self):
        return JSONResponse({
            "object": "list",
            "data": [{"id": m, "object": "model", "created": int(time.time()), "owned_by": "toolbaz"} for m in settings.MODELS]
        })

    async def close(self):
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
