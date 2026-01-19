from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
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
LOGOUT_URL = f"{BASE_URL}/disconnect.do"


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
        self._last_auth_failure_time: datetime | None = None
        self._last_successful_auth_time: datetime | None = None
        self._auth_retry_delay = timedelta(minutes=5)

    def _should_retry_auth(self) -> bool:
        """Check if enough time has passed since last auth failure to retry."""
        if self._last_auth_failure_time is None:
            return True
        
        time_since_failure = datetime.now() - self._last_auth_failure_time
        return time_since_failure >= self._auth_retry_delay

    def _clear_session(self) -> None:
        """Clear cookies and invalidate session."""
        try:
            self.session.cookie_jar.clear()
        except Exception as err:
            _LOGGER.debug("Error clearing cookies (non-critical): %s", err)
        self._authenticated = False
        _LOGGER.debug("Session cleared, cookies removed")

    async def async_authenticate(self) -> bool:
        if not self._should_retry_auth():
            time_remaining = self._auth_retry_delay - (datetime.now() - self._last_auth_failure_time)
            _LOGGER.warning(
                "Authentication failed recently, waiting %d seconds before retry",
                int(time_remaining.total_seconds())
            )
            return False

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
                    self._last_auth_failure_time = None
                    self._last_successful_auth_time = datetime.now()
                    _LOGGER.debug("Authentication successful, redirected to home.do, cookies set")
                    return True
                
                if response.status == 200:
                    html = await response.text()
                    html_lower = html.lower()
                    
                    if "session déjà ouverte" in html_lower or "session deja ouverte" in html_lower:
                        _LOGGER.warning("Session already open detected, attempting to close existing session")
                        self._last_auth_failure_time = datetime.now()
                        return False
                    
                    if "identifiant" in html_lower or "mot de passe" in html_lower:
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
            self._last_auth_failure_time = datetime.now()
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error during authentication: %s", err)
            self._last_auth_failure_time = datetime.now()
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
                if response.status == 404:
                    _LOGGER.warning("Received 404, session invalidated, clearing cookies")
                    self._clear_session()
                    return None
                
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
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
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_status_html(html)
                
                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
                        return None
                    async with self.session.get(
                        STATUS_URL,
                        headers=headers,
                        timeout=self._timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_status_html(html)
                
                response.raise_for_status()
                html = await response.text()
                _LOGGER.debug("HTML response length: %d", len(html))
                if not html or len(html.strip()) == 0:
                    _LOGGER.warning("Empty HTML response, session may be invalid")
                    self._clear_session()
                    return None
                parsed_data = self._parse_status_html(html)
                if not parsed_data.get("entities") and not parsed_data.get("sensors"):
                    _LOGGER.warning("No data parsed from HTML, session may be invalid")
                    self._clear_session()
                    return None
                _LOGGER.debug("Parsed status data: %s", parsed_data)
                return parsed_data
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.warning("404 error while getting status, session invalidated: %s", err)
                self._clear_session()
            else:
                _LOGGER.warning("HTTP error while getting status: %s", err)
                self._authenticated = False
            return None
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Timeout or connection error while getting status: %s", err)
            self._authenticated = False
            return None
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
            
            temperature_timeout = aiohttp.ClientTimeout(total=180)

            _LOGGER.debug("Fetching temperatures from %s", TEMPERATURES_URL)
            async with self.session.get(
                TEMPERATURES_URL,
                headers=headers,
                timeout=temperature_timeout,
                allow_redirects=False,
            ) as response:
                _LOGGER.debug("Temperatures response: %s", response.status)
                
                if response.status == 404:
                    _LOGGER.warning("Received 404, session invalidated, clearing cookies")
                    self._clear_session()
                    return None
                
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
                        return None
                    async with self.session.get(
                        TEMPERATURES_URL,
                        headers=headers,
                        timeout=temperature_timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        if retry_response.status == 302:
                            _LOGGER.error("Still receiving 302 after re-authentication")
                            return None
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_temperatures_html(html)

                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
                        return None
                    async with self.session.get(
                        TEMPERATURES_URL,
                        headers=headers,
                        timeout=temperature_timeout,
                        allow_redirects=False,
                    ) as retry_response:
                        retry_response.raise_for_status()
                        html = await retry_response.text()
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_temperatures_html(html)

                response.raise_for_status()
                html = await response.text()
                _LOGGER.debug("Temperatures HTML response length: %d", len(html))
                if not html or len(html.strip()) == 0:
                    _LOGGER.warning("Empty HTML response, session may be invalid")
                    self._clear_session()
                    return None
                parsed_data = self._parse_temperatures_html(html)
                if not parsed_data:
                    _LOGGER.warning("No temperature data parsed from HTML, session may be invalid")
                    self._clear_session()
                    return None
                _LOGGER.debug("Parsed temperatures data: %s", parsed_data)
                return parsed_data
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.warning("404 error while getting temperatures, session invalidated: %s", err)
                self._clear_session()
            else:
                _LOGGER.warning("HTTP error while getting temperatures: %s", err)
                self._authenticated = False
            return None
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Timeout or connection error while getting temperatures: %s", err)
            self._authenticated = False
            return None
        except Exception as err:
            _LOGGER.error("Failed to get temperatures: %s", err, exc_info=True)
            self._authenticated = False
            return None

    def _parse_temperatures_html(self, html: str) -> dict[str, Any]:
        """Parse temperature data from HTML table."""
        soup = BeautifulSoup(html, "html.parser")
        temperatures: dict[str, Any] = {}

        table = soup.find("table", class_=lambda x: x and "table" in x if x else False)
        if not table:
            _LOGGER.warning("Temperature table not found in HTML")
            _LOGGER.debug("HTML snippet (first 500 chars): %s", html[:500])
            return temperatures

        tbody = table.find("tbody")
        if not tbody:
            _LOGGER.warning("Temperature table tbody not found")
            return temperatures

        rows = tbody.find_all("tr")
        _LOGGER.debug("Found %d temperature rows", len(rows))
        
        if not rows:
            _LOGGER.warning("No temperature rows found in table")
            return temperatures

        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                room_name = cells[0].get_text(strip=True)
                temp_cell = cells[1]
                
                sup_tag = temp_cell.find("sup")
                if sup_tag:
                    sup_tag.decompose()
                
                temp_text = temp_cell.get_text(strip=True)
                
                if not room_name or not temp_text:
                    _LOGGER.debug("Skipping row with empty room_name or temp_text")
                    continue
                
                try:
                    temp_value_str = temp_text.replace("°C", "").replace("°", "").strip()
                    temp_value = float(temp_value_str)
                    sensor_id = room_name.lower().replace(" ", "_").replace("é", "e").replace("&eacute;", "e")
                    temperatures[sensor_id] = {
                        "name": room_name,
                        "value": temp_value,
                        "unit": "°C",
                    }
                    _LOGGER.debug("Found temperature: %s = %s°C", room_name, temp_value)
                except ValueError as err:
                    _LOGGER.warning("Could not parse temperature value '%s' for room '%s': %s", temp_text, room_name, err)

        if not temperatures:
            _LOGGER.warning("No temperatures parsed from HTML. Table structure: %s", table.prettify()[:500])
        
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
                
                if response.status == 404:
                    _LOGGER.warning("Received 404, session invalidated, clearing cookies")
                    self._clear_session()
                    return None
                
                if response.status == 302:
                    _LOGGER.warning("Received 302 redirect, authentication required")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
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
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_events_html(html)

                if response.status == 401 or response.status == 403:
                    _LOGGER.warning("Authentication expired, re-authenticating")
                    self._clear_session()
                    if not await self.async_authenticate():
                        _LOGGER.warning("Authentication retry blocked by rate limit, returning None")
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
                        if not html or len(html.strip()) == 0:
                            _LOGGER.warning("Empty HTML response, session may be invalid")
                            self._clear_session()
                            return None
                        return self._parse_events_html(html)

                response.raise_for_status()
                html = await response.text()
                _LOGGER.debug("Events HTML response length: %d", len(html))
                if not html or len(html.strip()) == 0:
                    _LOGGER.warning("Empty HTML response, session may be invalid")
                    self._clear_session()
                    return None
                parsed_data = self._parse_events_html(html)
                _LOGGER.debug("Parsed events data: %d events", len(parsed_data) if parsed_data else 0)
                return parsed_data
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Timeout or connection error while getting events: %s", err)
            self._authenticated = False
            return None
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

    def get_last_successful_auth_time(self) -> datetime | None:
        """Get the timestamp of the last successful authentication."""
        return self._last_successful_auth_time

    async def async_logout(self, force: bool = False) -> None:
        """Logout from Maison Protegee.
        
        Args:
            force: If True, logout even if not authenticated (useful for closing server-side sessions)
        """
        if not self._authenticated and not force:
            return

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant)",
            }

            _LOGGER.debug("Logging out from %s", LOGOUT_URL)
            async with self.session.get(
                LOGOUT_URL,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=True,
            ) as response:
                _LOGGER.debug("Logout response: %s", response.status)
                self._authenticated = False
        except Exception as err:
            _LOGGER.warning("Failed to logout: %s", err)
            self._authenticated = False

