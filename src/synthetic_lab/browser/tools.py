from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from synthetic_lab.contracts import Action, ActionStatus, Element, Observation, PersonaRecord, ToolResult


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "observe_page": {"name": "observe_page", "description": "Read the current page and available controls.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    "navigate": {"name": "navigate", "description": "Navigate within the allowed application origin.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}},
    "click": {"name": "click", "description": "Click an element from the latest page observation.", "parameters": {"type": "object", "properties": {"element_id": {"type": "string"}}, "required": ["element_id"], "additionalProperties": False}},
    "fill": {"name": "fill", "description": "Fill an input from the latest page observation.", "parameters": {"type": "object", "properties": {"element_id": {"type": "string"}, "value": {"type": "string"}}, "required": ["element_id", "value"], "additionalProperties": False}},
    "select_option": {"name": "select_option", "description": "Select a value in a select control from the latest observation.", "parameters": {"type": "object", "properties": {"element_id": {"type": "string"}, "value": {"type": "string"}}, "required": ["element_id", "value"], "additionalProperties": False}},
    "back": {"name": "back", "description": "Go back one page.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
}


def _error(action_id: str, code: str, message: str) -> ToolResult:
    return ToolResult(action_id=action_id, status=ActionStatus.ERROR, error_code=code, data={"message": message}, observed_at=datetime.now(timezone.utc))


class PlaywrightBrowserSession:
    """A browser context owned by one persona. Browser binaries are loaded lazily."""

    def __init__(self, run_id: str, session_id: str, origin: str, artifact_root: str | Path = "artifacts") -> None:
        self.run_id = run_id
        self.session_id = session_id
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("origin must be an absolute HTTP(S) URL")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.artifact_root = Path(artifact_root)
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None
        self._observation: Observation | None = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context()
        self.page = await self._context.new_page()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    def _check_origin(self, target: str) -> str:
        absolute = urljoin(self.origin + "/", target)
        if urlparse(absolute).netloc != urlparse(self.origin).netloc or urlparse(absolute).scheme != urlparse(self.origin).scheme:
            raise ValueError("navigation target is outside the allowed origin")
        return absolute

    async def observe(self) -> Observation:
        if self.page is None:
            raise RuntimeError("browser session has not started")
        url = self.page.url
        title = await self.page.title()
        visible_text = (await self.page.locator("body").inner_text())[:12000]
        elements: list[Element] = []
        locator = self.page.locator("button, a, input, select, textarea")
        count = min(await locator.count(), 100)
        for index in range(count):
            item = locator.nth(index)
            tag = await item.evaluate("node => node.tagName.toLowerCase()")
            role = "button" if tag == "button" else "link" if tag == "a" else "combobox" if tag == "select" else "textbox"
            name = (await item.get_attribute("aria-label")) or (await item.get_attribute("name")) or (await item.get_attribute("placeholder")) or (await item.inner_text())
            name = (name or "").strip()[:200]
            input_type = await item.get_attribute("type") if tag == "input" else None
            required = (await item.get_attribute("required")) is not None
            enabled = await item.is_enabled()
            filled = None
            if tag in {"input", "textarea", "select"}:
                # Never expose the value itself; passwords and personal data
                # stay in the browser context while the model gets progress.
                filled = bool(await item.input_value())
            fingerprint = f"{index}:{tag}:{name}:{url}"
            element_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
            allowed = ["click"] if role in {"button", "link"} else ["fill"] if role == "textbox" else ["select_option"]
            elements.append(Element(id=element_id, role=role, name=name, allowed_actions=allowed, input_type=input_type, filled=filled, required=required, enabled=enabled))
        self._observation = Observation(id=str(uuid.uuid4()), run_id=self.run_id, session_id=self.session_id, url=url, title=title, visible_text=visible_text, elements=elements, captured_at=datetime.now(timezone.utc))
        return self._observation

    async def _locator_for(self, element_id: str, action: str):
        if self.page is None or self._observation is None:
            raise ValueError("an observation is required before an element action")
        element = next((item for item in self._observation.elements if item.id == element_id), None)
        if element is None:
            raise ValueError("element is stale or not in the latest observation")
        if action not in element.allowed_actions:
            raise ValueError("action is not allowed for this element")
        index = next(i for i, item in enumerate(self._observation.elements) if item.id == element_id)
        locator = self.page.locator("button, a, input, select, textarea").nth(index)
        return locator

    async def execute(self, action: Action) -> ToolResult:
        try:
            if self.page is None:
                return _error(action.id, "browser_not_started", "browser session has not started")
            if action.tool_name == "observe_page":
                observation = await self.observe()
                return ToolResult(action_id=action.id, status=ActionStatus.SUCCESS, data=observation.model_dump(mode="json"), observed_at=datetime.now(timezone.utc))
            if action.tool_name == "navigate":
                await self.page.goto(self._check_origin(str(action.arguments["url"])))
            elif action.tool_name == "back":
                await self.page.go_back()
            elif action.tool_name in {"click", "fill", "select_option"}:
                element_id = str(action.arguments["element_id"])
                if action.observation_id != (self._observation.id if self._observation else None):
                    return _error(action.id, "stale_observation", "element IDs must come from the latest observation")
                locator = await self._locator_for(element_id, action.tool_name)
                if action.tool_name == "click":
                    await locator.click()
                elif action.tool_name == "fill":
                    await locator.fill(str(action.arguments["value"]))
                else:
                    await locator.select_option(str(action.arguments["value"]))
            else:
                return _error(action.id, "unknown_tool", action.tool_name)
            observation = await self.observe()
            return ToolResult(action_id=action.id, status=ActionStatus.SUCCESS, data=observation.model_dump(mode="json"), observed_at=datetime.now(timezone.utc))
        except KeyError as exc:
            return _error(action.id, "missing_argument", str(exc))
        except Exception as exc:
            return _error(action.id, "browser_action_failed", str(exc))

    async def save_state(self) -> Path:
        if self._context is None:
            raise RuntimeError("browser session has not started")
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        path = self.artifact_root / f"{self.run_id}-{self.session_id}-state.json"
        await self._context.storage_state(path=str(path))
        return path


class BrowserToolRegistry:
    def __init__(self, sessions: dict[str, Any]) -> None:
        self.sessions = sessions

    def list_allowed(self, persona: PersonaRecord) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[name] for name in persona.allowed_tool_names if name in TOOL_SCHEMAS]

    async def dispatch(self, persona: PersonaRecord, action: Action) -> ToolResult:
        if action.tool_name not in persona.allowed_tool_names:
            return _error(action.id, "unauthorized_tool", action.tool_name)
        if action.tool_name not in TOOL_SCHEMAS:
            return _error(action.id, "unknown_tool", action.tool_name)
        session = self.sessions.get(persona.id)
        if session is None:
            return _error(action.id, "missing_browser_session", persona.id)
        return await session.execute(action)
