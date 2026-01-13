from __future__ import annotations

import logging
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from .const import DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://maisonprotegee.orange.fr"
LOGIN_URL = f"{BASE_URL}/login/auth.do"
HOME_URL = f"{BASE_URL}/home.do"
STATUS_URL = f"{BASE_URL}/equipements/status/showBloc.do"


class MaisonProtegeeAPI:
    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self.username = username
        self.password = password
        self.session = session
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
        self._authenticated = False

    async def async_authenticate(self) -> bool:
        try:
            login_data = {
                "id": self.username,
                "pwd": self.password,
                "rememberme": "true",
                "rememberpwd": "true",
            }
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            async with self.session.post(
                LOGIN_URL,
                data=login_data,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=True,
            ) as response:
                final_url = str(response.url)
                cookies = self.session.cookie_jar.filter_cookies(BASE_URL)
                
                if HOME_URL in final_url or final_url.endswith("/home.do"):
                    self._authenticated = True
                    _LOGGER.debug("Authentication successful, redirected to home.do, cookies set")
                    return True
                
                _LOGGER.warning(
                    "Authentication failed: status %s, final URL: %s, cookies: %s",
                    response.status,
                    final_url,
                    bool(cookies),
                )
                return False
        except Exception as err:
            _LOGGER.error("Authentication failed: %s", err)
            return False

    async def async_get_status(self) -> dict[str, Any] | None:
        if not self._authenticated:
            if not await self.async_authenticate():
                return None

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            async with self.session.get(
                STATUS_URL,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        STATUS_URL,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        if retry_response.status == 302:
                            _LOGGER.error("Still receiving 302 after re-authentication")
                            return None
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_status_html(html)
                
                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        STATUS_URL,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_status_html(html)
                
                response.raise_for_status()
                html = await response.text()
                return self._parse_status_html(html)
        except Exception as err:
            _LOGGER.error("Failed to get status: %s", err)
            self._authenticated = False
            return None

    async def async_set_status(self, action: str) -> bool:
        if not self._authenticated:
            if not await self.async_authenticate():
                return False

        try:
            if action == "arm":
                command = "arm"
                previous_command = "100"
            elif action == "disarm":
                command = "disarm"
                previous_command = "101"
            else:
                _LOGGER.error("Unknown action: %s", action)
                return False

            action_url = f"{BASE_URL}/equipements/status/checkUpdateSystemStatus.do"
            params = {
                "command": command,
                "previousCommand": previous_command,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
                "Referer": STATUS_URL,
            }

            async with self.session.get(
                action_url,
                params=params,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return False
                    async with self.session.get(
                        action_url,
                        params=params,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        if retry_response.status == 302:
                            _LOGGER.error("Still receiving 302 after re-authentication")
                            return False
                        retry_response.raise_for_status()
                        return True
                
                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return False
                    async with self.session.get(
                        action_url,
                        params=params,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        return True
                
                response.raise_for_status()
                return True
        except Exception as err:
            _LOGGER.error("Failed to set status: %s", err)
            self._authenticated = False
            return False

    def _parse_status_html(self, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        status_data: dict[str, Any] = {
            "entities": {},
            "sensors": {},
        }

        status_text_elem = soup.find("span", class_="highlighted")
        if status_text_elem:
            status_text = status_text_elem.get_text(strip=True)
            status_data["alarm_status"] = status_text
            
            is_armed = "activée" in status_text.lower() or "armée" in status_text.lower()
            status_data["entities"]["alarm"] = {
                "name": "Alarme",
                "state": is_armed,
                "status_text": status_text,
            }

        icon_elem = soup.find("i", class_=lambda x: x and "icon-control" in x if x else False)
        if icon_elem:
            classes = icon_elem.get("class", [])
            if "icon-control-arm" in classes:
                status_data["entities"]["alarm"] = {
                    "name": "Alarme",
                    "state": True,
                    "status_text": "Alarme activée",
                }
            elif "icon-control-disarm" in classes:
                status_data["entities"]["alarm"] = {
                    "name": "Alarme",
                    "state": False,
                    "status_text": "Alarme désactivée",
                }

        status_rows = soup.find_all("div", class_="row status")
        for row in status_rows:
            status_span = row.find("span", class_="highlighted")
            if status_span:
                status_text = status_span.get_text(strip=True)
                icon = row.find("i", class_=lambda x: x and "icon-control" in x if x else False)
                if icon:
                    entity_id = "alarm"
                    status_data["entities"][entity_id] = {
                        "name": "Alarme",
                        "state": "désactivée" not in status_text.lower(),
                        "status_text": status_text,
                    }

        return status_data

