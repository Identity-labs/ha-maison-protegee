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
TEMPERATURES_URL = f"{BASE_URL}/equipements/temperatures/showTab.do"
LOGS_URL = f"{BASE_URL}/equipements/logs/showTable.do"


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
                from yarl import URL
                cookies = self.session.cookie_jar.filter_cookies(URL(BASE_URL))
                
                _LOGGER.debug(
                    "Login response: status=%s, final_url=%s, cookies=%s",
                    response.status,
                    final_url,
                    bool(cookies),
                )
                
                if HOME_URL in final_url or final_url.endswith("/home.do"):
                    self._authenticated = True
                    _LOGGER.debug("Authentication successful, redirected to home.do, cookies set")
                    return True
                
                if response.status == 200:
                    html = await response.text()
                    if "identifiant" in html.lower() or "mot de passe" in html.lower():
                        _LOGGER.warning("Authentication failed: Invalid credentials")
                        return False
                
                _LOGGER.warning(
                    "Authentication failed: status %s, final URL: %s, cookies: %s",
                    response.status,
                    final_url,
                    bool(cookies),
                )
                return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during authentication: %s", err)
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error during authentication: %s", err)
            raise

    async def async_get_status(self) -> dict[str, Any] | None:
        _LOGGER.debug("Getting status, authenticated: %s", self._authenticated)
        if not self._authenticated:
            _LOGGER.info("Not authenticated, authenticating...")
            if not await self.async_authenticate():
                _LOGGER.error("Authentication failed, cannot get status")
                return None

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            _LOGGER.debug("Fetching status from %s", STATUS_URL)
            async with self.session.get(
                STATUS_URL,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Status response: %s", response.status)
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
                _LOGGER.debug("HTML response length: %d", len(html))
                parsed_data = self._parse_status_html(html)
                _LOGGER.debug("Parsed status data: %s", parsed_data)
                return parsed_data
        except Exception as err:
            _LOGGER.error("Failed to get status: %s", err, exc_info=True)
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

    async def async_get_temperatures(self) -> dict[str, Any] | None:
        """Get temperature data from all devices."""
        _LOGGER.debug("Getting temperatures, authenticated: %s", self._authenticated)
        if not self._authenticated:
            _LOGGER.info("Not authenticated, authenticating...")
            if not await self.async_authenticate():
                _LOGGER.error("Authentication failed, cannot get temperatures")
                return None

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            _LOGGER.debug("Fetching temperatures from %s", TEMPERATURES_URL)
            async with self.session.get(
                TEMPERATURES_URL,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Temperatures response: %s", response.status)
                
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        TEMPERATURES_URL,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        if retry_response.status == 302:
                            _LOGGER.error("Still receiving 302 after re-authentication")
                            return None
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_temperatures_html(html)

                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        TEMPERATURES_URL,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_temperatures_html(html)

                response.raise_for_status()
                html = await response.text()
                _LOGGER.debug("Temperatures HTML response length: %d", len(html))
                parsed_data = self._parse_temperatures_html(html)
                _LOGGER.debug("Parsed temperatures data: %s", parsed_data)
                return parsed_data
        except Exception as err:
            _LOGGER.error("Failed to get temperatures: %s", err, exc_info=True)
            self._authenticated = False
            return None

    def _parse_temperatures_html(self, html: str) -> dict[str, Any]:
        """Parse temperature data from HTML table."""
        soup = BeautifulSoup(html, "html.parser")
        temperatures: dict[str, Any] = {}

        table = soup.find("table", class_="table")
        if not table:
            _LOGGER.warning("Temperature table not found in HTML")
            return temperatures

        tbody = table.find("tbody")
        if not tbody:
            _LOGGER.warning("Temperature table tbody not found")
            return temperatures

        rows = tbody.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                room_name = cells[0].get_text(strip=True)
                temp_cell = cells[1]
                temp_text = temp_cell.get_text(strip=True)
                
                try:
                    temp_value = float(temp_text.replace("°C", "").strip())
                    sensor_id = room_name.lower().replace(" ", "_").replace("é", "e")
                    temperatures[sensor_id] = {
                        "name": room_name,
                        "value": temp_value,
                        "unit": "°C",
                    }
                    _LOGGER.debug("Found temperature: %s = %s°C", room_name, temp_value)
                except ValueError:
                    _LOGGER.warning("Could not parse temperature value: %s", temp_text)

        return temperatures

    async def async_get_events(self, days: int = 30) -> list[dict[str, Any]] | None:
        """Get event logs from the API."""
        _LOGGER.debug("Getting events, authenticated: %s", self._authenticated)
        if not self._authenticated:
            _LOGGER.info("Not authenticated, authenticating...")
            if not await self.async_authenticate():
                _LOGGER.error("Authentication failed, cannot get events")
                return None

        try:
            from datetime import datetime, timedelta
            
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)
            
            params = {
                "fromDate": from_date.strftime("%d/%m/%Y"),
                "toDate": to_date.strftime("%d/%m/%Y"),
                "filters": "1",
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            _LOGGER.debug("Fetching events from %s with params %s", LOGS_URL, params)
            async with self.session.get(
                LOGS_URL,
                params=params,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Events response: %s", response.status)
                
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        LOGS_URL,
                        params=params,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        if retry_response.status == 302:
                            _LOGGER.error("Still receiving 302 after re-authentication")
                            return None
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_events_html(html)

                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._authenticated = False
                    if not await self.async_authenticate():
                        return None
                    async with self.session.get(
                        LOGS_URL,
                        params=params,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        return self._parse_events_html(html)

                response.raise_for_status()
                html = await response.text()
                _LOGGER.debug("Events HTML response length: %d", len(html))
                parsed_data = self._parse_events_html(html)
                _LOGGER.debug("Parsed events data: %d events", len(parsed_data) if parsed_data else 0)
                return parsed_data
        except Exception as err:
            _LOGGER.error("Failed to get events: %s", err, exc_info=True)
            self._authenticated = False
            return None

    def _parse_events_html(self, html: str) -> list[dict[str, Any]]:
        """Parse event logs from HTML table."""
        from datetime import datetime
        
        soup = BeautifulSoup(html, "html.parser")
        events: list[dict[str, Any]] = []

        table = soup.find("table", class_="table")
        if not table:
            _LOGGER.warning("Events table not found in HTML")
            return events

        tbody = table.find("tbody")
        if not tbody:
            _LOGGER.warning("Events table tbody not found")
            return events

        rows = tbody.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                icon_cell = cells[0]
                date_cell = cells[1]
                message_cell = cells[2]
                
                icon_elem = icon_cell.find("i")
                event_type = "unknown"
                if icon_elem:
                    classes = icon_elem.get("class", [])
                    if "icon-control-arm" in classes:
                        event_type = "arm"
                    elif "icon-control-disarm" in classes:
                        event_type = "disarm"
                
                date_text = date_cell.get_text(strip=True)
                message_text = message_cell.get_text(strip=True)
                
                try:
                    date_str = date_text.replace("à", "").replace("h", ":").strip()
                    event_date = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
                except ValueError:
                    _LOGGER.warning("Could not parse event date: %s", date_text)
                    event_date = None
                
                event = {
                    "type": event_type,
                    "date": event_date.isoformat() if event_date else None,
                    "date_text": date_text,
                    "message": message_text,
                }
                events.append(event)
                _LOGGER.debug("Found event: %s", event)

        return events

