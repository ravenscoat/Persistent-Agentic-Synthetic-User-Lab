"""Opt-in browser proof for authenticated Playwright state recovery."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from synthetic_lab.browser.tools import PlaywrightBrowserSession
from synthetic_lab.contracts import Action


def _element_id(observation, name: str) -> str:
    return next(element.id for element in observation.elements if element.name == name)


@pytest.mark.asyncio
async def test_browser_state_and_checkpoint_url_restore_authenticated_session(tmp_path) -> None:
    """A fresh browser reaches the saved post-signup page without logging in again."""
    origin = os.getenv("SUL_BROWSER_RECOVERY_URL")
    if not origin:
        pytest.skip("set SUL_BROWSER_RECOVERY_URL to run browser checkpoint recovery proof")
    first = PlaywrightBrowserSession("browser-recovery", "persona", origin, artifact_root=tmp_path)
    try:
        await first.start()
        await first.page.goto(origin.rstrip("/") + "/signup")
        observation = await first.observe()
        email = await first.execute(Action(id="email", tool_name="fill", arguments={"element_id": _element_id(observation, "email"), "value": "recovery@example.test"}, observation_id=observation.id))
        observation = await first.observe()
        password = await first.execute(Action(id="password", tool_name="fill", arguments={"element_id": _element_id(observation, "password"), "value": "safe-test-password"}, observation_id=observation.id))
        assert email.status.value == password.status.value == "success"
        observation = await first.observe()
        submitted = await first.execute(Action(id="submit", tool_name="click", arguments={"element_id": _element_id(observation, "Create account")}, observation_id=observation.id))
        assert submitted.status.value == "success"
        checkpoint_url = first.page.url
        state_path = await first.save_state()
    finally:
        await first.close()

    recovered = PlaywrightBrowserSession("browser-recovery", "persona", origin, artifact_root=tmp_path, storage_state_path=state_path)
    try:
        await recovered.start()
        await recovered.page.goto(checkpoint_url)
        observation = await recovered.observe()
        assert observation.url.endswith("/dashboard")
        assert "Good afternoon" in observation.visible_text
    finally:
        await recovered.close()
