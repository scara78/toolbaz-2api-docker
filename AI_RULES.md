# 🤖 Reguli de Dezvoltare AI pentru Toolbaz-2API

Acest document stabilește standardele de arhitectură și regulile de utilizare a bibliotecilor pentru a asigura consistența proiectului.

## 🛠️ Stivă Tehnologică

- **Framework Backend**: FastAPI (Python 3.10+) pentru performanță ridicată și compatibilitate OpenAI.
- **Automatizare Browser**: Playwright (Python) pentru interacțiuni headless și execuție JavaScript.
- **Gestionare Configurare**: Pydantic Settings pentru variabile de mediu tipizate.
- **Networking Asincron**: `httpx` pentru toate cererile HTTP non-blocking.
- **Logare**: Loguru pentru diagnoză structurată.
- **Frontend**: Pagina statică HTML/JS/CSS în `static/` pentru o experiență de cockpit simplă.

## 📏 Reguli de Implementare

### 1. API & Rute
- Folosiți întotdeauna **FastAPI** pentru rute noi.
- Mențineți compatibilitatea OpenAI pentru toate răspunsurile de tip `/v1/chat/completions`.
- Utilizați `sse_utils.py` pentru toate răspunsurile de tip streaming (Server-Sent Events).

### 2. Automatizare Browser
- Toate interacțiunile cu browserul **trebuie** să treacă prin clasa `BrowserWorker`.
- Nu instanțiați browsere Playwright direct în rute; folosiți pool-ul din `ToolbazProvider`.

### 3. Erori
- Logați eșecurile critice cu `logger.error` înainte de a ridica `HTTPException`.