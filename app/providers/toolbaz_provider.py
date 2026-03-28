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

            # Creare context incognito
            self.context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="ro-RO",
                timezone_id="Europe/Bucharest",
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True
            )
            
            self.page = await self.context.new_page()
            # Ascunde caracteristicile webdriver
            await self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            
            # Preîncălzire (cu mecanism de reîncercare)
            logger.info(f"🔧 [Worker-{self.id}] Se preîncălzește...")
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await asyncio.sleep(random.uniform(1, 2))
                    await self.page.goto(
                        "https://toolbaz.com/writer/chat-gpt-alternative", 
                        wait_until="domcontentloaded", 
                        timeout=45000
                    )
                    break 
                except PlaywrightError as e:
                    if "ERR_CONNECTION_CLOSED" in str(e) or "Timeout" in str(e):
                        logger.warning(f"⚠️ [Worker-{self.id}] Preîncălzire eșuată (încercarea {attempt+1}/{max_retries}): {e}")
                        if attempt == max_retries - 1:
                            raise e 
                        await asyncio.sleep(5) 
                    else:
                        raise e

            self.created_at = time.time()
            self.uses_count = 0
            logger.info(f"✅ [Worker-{self.id}] Pregătit")
            return True
        except Exception as e:
            logger.error(f"❌ [Worker-{self.id}] Inițializare eșuată: {e}")
            await self.close()
            return False

    async def get_token_data(self):
        """Obține Token-ul din această fereastră specifică"""
        if not self.page or self.page.is_closed():
            success = await self.init()
            if not success:
                return {"error": "Reinițializarea worker-ului a eșuat"}

        try:
            await self.page.wait_for_function("typeof window.xA1pY === 'function' || typeof xA1pY === 'function'", timeout=5000)
        except:
            try:
                logger.warning(f"⚠️ [Worker-{self.id}] Funcția nu este gata, se reîmprospătează pagina...")
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                return {"error": f"Reîmprospătare eșuată: {str(e)}"}

        result = await self.page.evaluate("""() => {
            try {
                function getCookie(name) {
                    const value = `; ${document.cookie}`;
                    const parts = value.split(`; ${name}=`);
                    if (parts.length === 2) return parts.pop().split(';').shift();
                    return null;
                }
                let sessionId = getCookie("SessionID");
                if (!sessionId) {
                    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
                    sessionId = "";
                    for (let i = 0; i < 36; i++) sessionId += chars.charAt(Math.floor(Math.random() * chars.length));
                    document.cookie = `SessionID=${sessionId}; path=/`;
                }
                
                let token = "";
                if (typeof window.xA1pY === 'function') token = window.xA1pY();
                else if (typeof xA1pY === 'function') token = xA1pY();
                else return { error: "xA1pY lipsește" };

                return { sessionId, token };
            } catch (e) { return { error: e.toString() }; }
        }""")
        
        self.uses_count += 1
        return result

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
        """Pornește cluster-ul de browsere"""
        logger.info(f"🚀 Se pornește clusterul de browsere (concurență: {settings.BROWSER_POOL_SIZE})...")
        self.playwright = await async_playwright().start()
        
        launch_args = [
            "--no-sandbox", 
            "--disable-setuid-sandbox", 
            "--disable-dev-shm-usage", 
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled"
        ]
        
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=launch_args
        )

        for i in range(settings.BROWSER_POOL_SIZE):
            worker = BrowserWorker(self.browser)
            asyncio.create_task(self._init_and_push_worker(worker))
            await asyncio.sleep(3)
        
        logger.info(f"✅ Comenzile de pornire pentru browsere au fost trimise...")

    async def _init_and_push_worker(self, worker: BrowserWorker):
        success = await worker.init()
        if success:
            await self.pool.put(worker)
        else:
            logger.warning(f"⚠️ Worker-{worker.id} inițializare eșuată, reîncercare în 10 secunde...")
            await asyncio.sleep(10)
            await self._init_and_push_worker(worker)

    async def _wait_for_rate_limit(self):
        """Limitator de rată: maxim 5 cereri pe minut"""
        async with self.rate_limit_lock:
            current_time = time.time()
            self.request_timestamps = [t for t in self.request_timestamps if current_time - t < 60]
            
            MAX_REQUESTS_PER_MINUTE = 4 
            
            if len(self.request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                oldest_request = self.request_timestamps[0]
                wait_time = 60 - (current_time - oldest_request) + 1
                if wait_time > 0:
                    logger.warning(f"🚦 Limită de rată activă (5 cereri/min), se așteaptă {wait_time:.2f} secunde...")
                    await asyncio.sleep(wait_time)
            
            self.request_timestamps.append(time.time())

    def _clean_response_text(self, text: str) -> str:
        if not text: return ""
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        text = html.unescape(text)
        text = re.sub(r'^\[model:.*?\]\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^Toolbaz.*?:', '', text, flags=re.IGNORECASE)
        return text.strip()

    async def chat_completion(self, request_data: Dict[str, Any]):
        model = request_data.get("model", settings.DEFAULT_MODEL)
        messages = request_data.get("messages", [])
        stream = request_data.get("stream", True)
        
        last_user_content = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "Salut")
        padding = "\u3164"
        formatted_text = f"{padding} : {last_user_content}{padding}"

        logger.info(f"⏳ Se așteaptă fereastră browser disponibilă (disponibile acum: {self.pool.qsize()})...")
        worker: BrowserWorker = await self.pool.get()
        
        try:
            logger.info(f"🤖 Se folosește [Worker-{worker.id}] pentru procesare...")
            
            if worker.uses_count > settings.CONTEXT_MAX_USES:
                logger.info(f"♻️ [Worker-{worker.id}] a atins limita de utilizări, se reconstruiește...")
                await worker.init()

            security_data = await worker.get_token_data()
            if security_data.get("error"):
                logger.error(f"❌ [Worker-{worker.id}] Eroare obținere Token: {security_data.get('error')}")
                await worker.init()
                security_data = await worker.get_token_data()
                if security_data.get("error"):
                    raise Exception(f"Generare Token eșuată: {security_data['error']}")

            session_id = security_data["sessionId"]
            payload_token = security_data["token"]

            await self._wait_for_rate_limit()

            async with httpx.AsyncClient() as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Origin": "https://toolbaz.com",
                    "Referer": "https://toolbaz.com/writer/chat-gpt-alternative",
                    "X-Requested-With": "XMLHttpRequest",
                    "Cookie": f"SessionID={session_id}"
                }

                token_resp = await client.post(
                    self.api_token_url,
                    data={"session_id": session_id, "token": payload_token},
                    headers=headers,
                    timeout=20
                )
                
                if token_resp.status_code != 200:
                    raise ValueError(f"Eroare API Token (Cod: {token_resp.status_code})")
                
                token_json = token_resp.json()
                if not token_json.get("success"):
                    raise ValueError(f"API Token a refuzat cererea: {token_json}")
                
                capcha_token = token_json["token"]

                chat_resp = await client.post(
                    self.api_writing_url,
                    data={
                        "text": formatted_text,
                        "capcha": capcha_token,
                        "model": model,
                        "session_id": session_id
                    },
                    headers=headers,
                    timeout=120
                )
                
                if chat_resp.status_code == 400 and "quota limit" in chat_resp.text:
                    logger.warning("⚠️ Limită de cotă API atinsă, se returnează 429")
                    await self.pool.put(worker)
                    return JSONResponse({"error": "Limita de rată a fost depășită (5 cereri/min)."}, status_code=429)

                if chat_resp.status_code != 200:
                    raise ValueError(f"Eroare API Scriere: {chat_resp.status_code} - {chat_resp.text[:100]}")
                
                clean_text = self._clean_response_text(chat_resp.text)
                request_id = f"chatcmpl-{uuid.uuid4()}"

                if not stream:
                    await self.pool.put(worker)
                    logger.info(f"🔙 [Worker-{worker.id}] a fost eliberat")
                    return JSONResponse({
                        "id": request_id,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": clean_text}, "finish_reason": "stop"}]
                    })

                async def stream_generator():
                    try:
                        chunk_size = 20
                        for i in range(0, len(clean_text), chunk_size):
                            part = clean_text[i:i+chunk_size]
                            yield create_sse_data(create_chat_completion_chunk(request_id, model, part))
                            await asyncio.sleep(0.02)
                        yield create_sse_data(create_chat_completion_chunk(request_id, model, "", "stop"))
                        yield DONE_CHUNK
                    finally:
                        await self.pool.put(worker)
                        logger.info(f"🔙 [Worker-{worker.id}] a fost eliberat (stream finalizat)")

                return StreamingResponse(stream_generator(), media_type="text/event-stream")

        except Exception as e:
            logger.error(f"❌ [Worker-{worker.id}] Eroare critică: {e}")
            asyncio.create_task(self._recycle_worker(worker))
            raise HTTPException(status_code=500, detail=str(e))

    async def _recycle_worker(self, worker: BrowserWorker):
        """Reciclează și resetează un Worker în fundal"""
        logger.info(f"🔧 [Worker-{worker.id}] Se resetează în fundal...")
        await asyncio.sleep(5)
        success = await worker.init()
        if success:
            await self.pool.put(worker)
            logger.info(f"✅ [Worker-{worker.id}] Resetat cu succes și eliberat în pool")
        else:
            logger.error(f"💀 [Worker-{worker.id}] Resetare eșuată, reîncercare...")
            await asyncio.sleep(10)
            await self._recycle_worker(worker)

    async def get_models(self):
        return JSONResponse({
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": int(time.time()), "owned_by": "toolbaz"}
                for m in settings.MODELS
            ]
        })

    async def close(self):
        while not self.pool.empty():
            worker = await self.pool.get()
            await worker.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()