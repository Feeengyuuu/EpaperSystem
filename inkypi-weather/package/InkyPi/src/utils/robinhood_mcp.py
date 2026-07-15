"""Read-only client for Robinhood's official Trading MCP server."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


DEFAULT_TOKEN_PATH = "/var/lib/inkypi/secrets/robinhood_mcp.json"
MCP_PROTOCOL_VERSION = "2025-06-18"
READ_ONLY_TOOLS = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_quotes",
        "get_equity_historicals",
    }
)


class RobinhoodMCPError(RuntimeError):
    """Raised when an official Robinhood MCP refresh cannot be trusted."""


class RobinhoodMCPClient:
    """Minimal Streamable HTTP MCP client restricted to read-only tools."""

    def __init__(self, token_path=None, *, http=None, timeout=30):
        configured_path = token_path or os.getenv("ROBINHOOD_MCP_TOKEN_FILE") or DEFAULT_TOKEN_PATH
        self.token_path = Path(configured_path).expanduser()
        self.http = http or requests.Session()
        self.timeout = timeout
        self._credentials = None
        self._session_id = None
        self._request_id = 0
        self._connected = False

    def _load_credentials(self):
        if self._credentials is not None:
            return self._credentials
        try:
            credentials = json.loads(self.token_path.read_text(encoding="utf-8"))
        except Exception as error:
            raise RobinhoodMCPError("Robinhood MCP credentials are unavailable") from error
        token = credentials.get("token")
        registration = credentials.get("registration")
        if not isinstance(token, dict) or not token.get("access_token"):
            raise RobinhoodMCPError("Robinhood MCP credentials contain no access token")
        if not isinstance(registration, dict) or not registration.get("client_id"):
            raise RobinhoodMCPError("Robinhood MCP credentials contain no OAuth client id")
        if not credentials.get("mcp_url") or not credentials.get("token_url"):
            raise RobinhoodMCPError("Robinhood MCP credentials contain incomplete endpoints")
        self._credentials = credentials
        return credentials

    def _save_credentials(self, credentials):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_path.with_suffix(self.token_path.suffix + ".tmp")
        payload = json.dumps(credentials, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.token_path)
            try:
                os.chmod(self.token_path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def refresh_access_token(self):
        credentials = self._load_credentials()
        old_token = credentials["token"]
        refresh_token = str(old_token.get("refresh_token") or "")
        if not refresh_token:
            raise RobinhoodMCPError("Robinhood MCP credentials contain no refresh token")
        response = self.http.post(
            credentials["token_url"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": credentials["registration"]["client_id"],
                "resource": credentials["mcp_url"],
            },
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            refreshed = response.json()
        except Exception as error:
            raise RobinhoodMCPError("Robinhood OAuth token refresh failed") from error
        access_token = str(refreshed.get("access_token") or "")
        if not access_token:
            raise RobinhoodMCPError("Robinhood OAuth token refresh returned no access token")
        now = time.time()
        merged = dict(old_token)
        merged.update(refreshed)
        if not refreshed.get("refresh_token"):
            merged["refresh_token"] = refresh_token
        merged["obtained_at"] = now
        try:
            expires_in = float(refreshed.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        if expires_in > 0:
            merged["expires_at"] = now + expires_in
        credentials["token"] = merged
        self._credentials = credentials
        self._save_credentials(credentials)
        return access_token

    def _access_token(self):
        credentials = self._load_credentials()
        token = credentials["token"]
        try:
            expires_at = float(token.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at and expires_at <= time.time() + 60:
            return self.refresh_access_token()
        return str(token["access_token"])

    @staticmethod
    def _response_json(response):
        if not response.content:
            return {}
        content_type = str(response.headers.get("Content-Type") or "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RobinhoodMCPError("Robinhood MCP response contained no data event")
        return response.json()

    def _post(self, payload, *, retry_auth=True):
        credentials = self._load_credentials()
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self.http.post(
            credentials["mcp_url"],
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            self.refresh_access_token()
            return self._post(payload, retry_auth=False)
        try:
            response.raise_for_status()
            result = self._response_json(response)
        except RobinhoodMCPError:
            raise
        except Exception as error:
            raise RobinhoodMCPError(f"Robinhood MCP HTTP request failed ({response.status_code})") from error
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if isinstance(result, dict) and result.get("error"):
            error = result["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RobinhoodMCPError(f"Robinhood MCP error: {message or 'unknown error'}")
        return result

    def _rpc(self, method, params=None, *, notification=False):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            self._request_id += 1
            payload["id"] = self._request_id
        return self._post(payload)

    def _connect(self):
        if self._connected:
            return
        initialized = self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "InkyPi Money Read-Only", "version": "1.0"},
            },
        )
        if not isinstance(initialized, dict) or not initialized.get("result"):
            raise RobinhoodMCPError("Robinhood MCP initialization failed")
        self._rpc("notifications/initialized", {}, notification=True)
        self._connected = True

    @staticmethod
    def _tool_payload(response):
        result = response.get("result", {}) if isinstance(response, dict) else {}
        if result.get("isError"):
            raise RobinhoodMCPError("Robinhood MCP tool reported an error")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        for item in result.get("content", []):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                continue
        raise RobinhoodMCPError("Robinhood MCP tool returned no structured data")

    def call_tool(self, name, arguments=None):
        if name not in READ_ONLY_TOOLS:
            raise RobinhoodMCPError(f"Robinhood MCP tool is not allowlisted for read-only use: {name}")
        self._connect()
        response = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return self._tool_payload(response)

    @staticmethod
    def _walk_dicts(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from RobinhoodMCPClient._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from RobinhoodMCPClient._walk_dicts(child)

    @staticmethod
    def _number(value, *, field):
        try:
            number = float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError) as error:
            raise RobinhoodMCPError(f"Robinhood MCP returned an invalid {field}") from error
        if not math.isfinite(number):
            raise RobinhoodMCPError(f"Robinhood MCP returned a non-finite {field}")
        return number

    @staticmethod
    def _optional_number(value):
        if value in (None, ""):
            return None
        try:
            number = float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _cursor(payload):
        for row in RobinhoodMCPClient._walk_dicts(payload):
            for key in ("next", "next_cursor"):
                value = row.get(key)
                if not value:
                    continue
                text = str(value)
                if text.startswith("http"):
                    parsed = parse_qs(urlparse(text).query).get("cursor", [])
                    return parsed[0] if parsed else None
                return text
        return None

    @staticmethod
    def _timestamp_sort_key(value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            return None

    @classmethod
    def _normalized_quote(cls, row):
        symbol = str(row.get("symbol") or "").strip().upper()
        candidates = []
        for price_key, time_key, extended in (
            ("last_trade_price", "venue_last_trade_time", False),
            ("last_non_reg_trade_price", "venue_last_non_reg_trade_time", True),
        ):
            price = cls._optional_number(row.get(price_key))
            timestamp = str(row.get(time_key) or "").strip()
            sort_key = cls._timestamp_sort_key(timestamp)
            if price is not None and price > 0 and sort_key is not None:
                candidates.append((sort_key, price, timestamp, extended))
        if not symbol or not candidates:
            return None
        _sort_key, price, timestamp, extended = max(candidates, key=lambda item: item[0])
        previous_close = cls._optional_number(
            row.get("adjusted_previous_close") or row.get("previous_close")
        )
        if previous_close is None or previous_close <= 0:
            raise RobinhoodMCPError(f"Robinhood official quote has no previous close for {symbol}")
        return {
            "symbol": symbol,
            "price": price,
            "previous_close": previous_close,
            "timestamp": timestamp,
            "extended_hours": extended,
        }

    def _account_number(self, account_hash):
        matches = []
        for row in self._walk_dicts(self.call_tool("get_accounts")):
            account_number = str(row.get("account_number") or row.get("accountNumber") or "").strip()
            if not account_number:
                continue
            digest = hashlib.sha256(account_number.encode("utf-8")).hexdigest()[:12]
            if digest == account_hash:
                matches.append(account_number)
        unique = list(dict.fromkeys(matches))
        if len(unique) != 1:
            raise RobinhoodMCPError("Robinhood account hash did not resolve to exactly one account")
        return unique[0]

    def _positions(self, account_number):
        positions = {}
        cursor = None
        seen_cursors = set()
        while True:
            arguments = {"account_number": account_number}
            if cursor:
                arguments["cursor"] = cursor
            payload = self.call_tool("get_equity_positions", arguments)
            for row in self._walk_dicts(payload):
                symbol = str(row.get("symbol") or "").strip().upper()
                quantity = self._optional_number(row.get("quantity"))
                if symbol and quantity is not None and quantity > 0:
                    positions[symbol] = quantity
            cursor = self._cursor(payload)
            if not cursor:
                break
            if cursor in seen_cursors:
                raise RobinhoodMCPError("Robinhood positions pagination repeated a cursor")
            seen_cursors.add(cursor)
        if not positions:
            raise RobinhoodMCPError("Robinhood account returned no positive equity positions")
        return [{"symbol": symbol, "quantity": quantity} for symbol, quantity in positions.items()]

    def _quotes(self, symbols):
        quotes = {}
        for start in range(0, len(symbols), 20):
            batch = symbols[start : start + 20]
            payload = self.call_tool("get_equity_quotes", {"symbols": batch})
            for row in self._walk_dicts(payload):
                quote = self._normalized_quote(row)
                if quote:
                    quotes[quote["symbol"]] = quote
        missing = [symbol for symbol in symbols if symbol not in quotes]
        if missing:
            raise RobinhoodMCPError(
                "Robinhood MCP is missing an official quote for held symbol(s): " + ", ".join(missing)
            )
        return quotes

    def _portfolio_meta(self, account_number):
        payload = self.call_tool("get_portfolio", {"account_number": account_number})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RobinhoodMCPError("Robinhood portfolio response has no data object")
        buying_power = data.get("buying_power")
        if not isinstance(buying_power, dict):
            buying_power = {}
        currency = str(data.get("currency") or buying_power.get("display_currency") or "USD").upper()
        return {
            "account_value": self._number(data.get("total_value"), field="account value"),
            "cash_balance": self._number(data.get("cash"), field="cash balance"),
            "buying_power": self._number(buying_power.get("buying_power"), field="buying power"),
            "pending_deposits": self._number(
                data.get("pending_deposits") or 0,
                field="pending deposits",
            ),
            "currency": currency,
        }

    @staticmethod
    def _historical_window(period, now=None):
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc).replace(microsecond=0)
        period = str(period or "1mo").strip().lower()
        if period == "1d":
            return current - timedelta(days=3), current, "5minute"
        if period == "5d":
            return current - timedelta(days=10), current, "30minute"
        if period == "3mo":
            return current - timedelta(days=95), current, "day"
        if period == "6mo":
            return current - timedelta(days=190), current, "day"
        if period == "1y":
            return current - timedelta(days=370), current, "day"
        if period == "ytd":
            return current.replace(month=1, day=1, hour=0, minute=0, second=0), current, "day"
        return current - timedelta(days=32), current, "day"

    @staticmethod
    def _rfc3339(value):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _histories(self, symbols, period, *, now=None):
        start_time, end_time, interval = self._historical_window(period, now=now)
        histories = {}
        for start in range(0, len(symbols), 10):
            batch = symbols[start : start + 10]
            payload = self.call_tool(
                "get_equity_historicals",
                {
                    "symbols": batch,
                    "start_time": self._rfc3339(start_time),
                    "end_time": self._rfc3339(end_time),
                    "interval": interval,
                    "bounds": "regular",
                    "adjustment_type": "split",
                },
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                raise RobinhoodMCPError("Robinhood historical response has no results")
            for result in results:
                if not isinstance(result, dict):
                    continue
                symbol = str(result.get("symbol") or "").strip().upper()
                if symbol not in batch:
                    continue
                points = []
                for bar in result.get("bars") or []:
                    if not isinstance(bar, dict):
                        continue
                    timestamp = str(bar.get("begins_at") or "").strip()
                    close = self._optional_number(bar.get("close_price"))
                    if timestamp and close is not None and close > 0:
                        points.append((timestamp, close))
                points = list(dict(points).items())
                if len(points) >= 2:
                    histories[symbol] = points
        missing = [symbol for symbol in symbols if symbol not in histories]
        if missing:
            raise RobinhoodMCPError(
                "Robinhood MCP is missing official price history for held symbol(s): "
                + ", ".join(missing)
            )
        return histories

    def fetch_snapshot(self, account_hash, period="1mo", *, now=None):
        account_hash = str(account_hash or "").strip().lower()
        if len(account_hash) != 12 or any(character not in "0123456789abcdef" for character in account_hash):
            raise RobinhoodMCPError("Robinhood account hash must be a 12-character SHA-256 prefix")
        account_number = self._account_number(account_hash)
        positions = self._positions(account_number)
        symbols = [position["symbol"] for position in positions]
        quotes = self._quotes(symbols)
        histories = self._histories(symbols, period, now=now)
        portfolio_meta = self._portfolio_meta(account_number)
        return {
            "account_hash": account_hash,
            "positions": positions,
            "quotes": quotes,
            "histories": histories,
            "portfolio_meta": portfolio_meta,
        }
