# 🤖 Toolbaz-2API AI Development Rules

This document outlines the architectural standards and library usage rules for the Toolbaz-2API project to ensure consistency and maintainability.

## 🛠️ Tech Stack

- **Backend Framework**: FastAPI (Python 3.10+) for high-performance, asynchronous API endpoints and OpenAI compatibility.
- **Browser Automation**: Playwright (Python) for headless browser interactions and executing dynamic JavaScript in the background.
- **Configuration Management**: Pydantic Settings for type-safe environment variable parsing and application configuration.
- **Asynchronous Networking**: `httpx` for all non-blocking HTTP requests to external services.
- **Concurrency & Pooling**: Custom `BrowserWorker` implementation using `asyncio.Queue` for efficient browser instance management.
- **Logging**: Loguru for structured, color-coded, and easily searchable diagnostic logs.
- **Containerization**: Docker & Docker Compose for standardized deployment and shared memory (`shm_size`) optimization.
- **Frontend**: Single-page static HTML/JS/CSS located in `static/` for a lightweight "Developer Cockpit" experience.

## 📏 Library & Implementation Rules

### 1. API & Routing
- Always use **FastAPI** for new endpoints.
- Maintain OpenAI compatibility for all `/v1/chat/completions` responses.
- Use the `sse_utils.py` for all streaming (Server-Sent Events) responses.

### 2. Browser Automation
- All browser interactions **must** go through the `BrowserWorker` class.
- Do not instantiate Playwright browsers directly in routes; use the `ToolbazProvider` pool.
- Use `page.evaluate()` for JavaScript-based token extraction to bypass complex obfuscation.

### 3. State & Config
- Add new settings to `app/core/config.py` within the `Settings` class.
- Never hardcode credentials; always use `.env` files via Pydantic.

### 4. Concurrency
- Use `async`/`await` for all I/O bound tasks (database, network, browser).
- Implement rate limiting at the Provider level using `asyncio.Lock` to respect upstream quotas.

### 5. Error Handling
- Errors should bubble up to the FastAPI exception handlers.
- Log critical failures with `logger.error` before raising `HTTPException`.

### 6. Frontend
- Keep the `static/index.html` simple and focused on testing the API.
- Use vanilla JavaScript and CSS to avoid build-step complexities for this specific project.