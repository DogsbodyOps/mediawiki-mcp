"""
wiki_client.py — A thin wrapper around the MediaWiki action API.

MediaWiki's API lives at /api.php and uses an "action" parameter to
select what to do. All requests use a persistent requests.Session so
the login cookie is automatically sent on every subsequent call.

Key auth flow:
  1. Fetch a login token   (stateless GET)
  2. POST credentials      (sets session cookie)
  3. Fetch a CSRF token    (needed for any write operation)
  4. Use CSRF token in edit/delete/etc calls
"""

import re

import pyotp
import requests


class WikiClient:
    def __init__(self, base_url: str, username: str, password: str, totp_secret: str):
        """
        :param base_url:     Root URL of the wiki, e.g. https://wiki.example.com
        :param username:     Bot account username (from Special:BotPasswords)
        :param password:     Bot account password
        :param totp_secret:  Base32 TOTP secret for two-factor authentication
        """
        self.api_url = base_url.rstrip("/") + "/api.php"
        self.username = username
        self.password = password
        self.totp = pyotp.TOTP(totp_secret)

        # A Session persists cookies across requests — essential for auth
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MediaWiki-MCP-Server/1.0"})

        # Log in immediately on construction so all tool calls are authenticated
        self._login()

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def _login(self):
        """
        Two-step MediaWiki clientlogin with TOTP 2FA:
          Step 1 — submit username + password
          Step 2 — submit the current TOTP code
        """
        # Fetch login token
        r = self.session.get(self.api_url, params={
            "action": "query",
            "meta":   "tokens",
            "type":   "login",
            "format": "json",
        })
        r.raise_for_status()
        login_token = r.json()["query"]["tokens"]["logintoken"]

        # Step 1: submit credentials
        r = self.session.post(self.api_url, data={
            "action":                "clientlogin",
            "loginreturnurl":        "https://localhost",
            "logintoken":            login_token,
            "username":              self.username,
            "password":              self.password,
            "format":                "json",
        })
        r.raise_for_status()
        result = r.json().get("clientlogin", {})

        if result.get("status") == "PASS":
            return  # No 2FA required (unlikely given config, but handle it)

        if result.get("status") != "UI":
            raise RuntimeError(f"MediaWiki login failed at step 1: {result}")

        # Step 2: submit TOTP code, trying adjacent time windows to handle clock drift.
        # Range covers ±5 minutes — necessary for Docker/WSL2 environments where the
        # container clock can drift significantly from the wiki server's clock.
        import time
        offsets = [i * 30 for i in range(0, 11)]          # 0, 30, 60 ... 300
        offsets += [-i * 30 for i in range(1, 11)]        # -30, -60 ... -300
        for offset in offsets:
            r = self.session.post(self.api_url, data={
                "action":        "clientlogin",
                "logintoken":    login_token,
                "logincontinue": "1",
                "OATHToken":     self.totp.at(time.time() + offset),
                "format":        "json",
            })
            r.raise_for_status()
            result = r.json().get("clientlogin", {})
            if result.get("status") == "PASS":
                return
            if result.get("status") != "UI":
                break  # Hard failure, no point retrying

        raise RuntimeError(f"MediaWiki 2FA login failed: {result}")

    def _get_csrf_token(self) -> str:
        """
        Fetch a CSRF token. Required before any write operation (edit, move, etc.).
        The token is tied to the current session, so we fetch a fresh one each time
        to avoid stale-token errors on long-running sessions.
        """
        r = self.session.get(self.api_url, params={
            "action": "query",
            "meta":   "tokens",
            "format": "json",
        })
        r.raise_for_status()
        return r.json()["query"]["tokens"]["csrftoken"]

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def get_page(self, title: str) -> dict:
        """
        Fetch the wikitext source of a page by title.

        Returns a dict:
          {"title": str, "content": str, "exists": bool}

        The "content" field is raw wikitext (the same thing you see when
        you click "Edit" in the browser).
        """
        r = self.session.get(self.api_url, params={
            "action":  "query",
            "titles":  title,
            "prop":    "revisions",
            "rvprop":  "content",   # Return the wikitext
            "rvslots": "main",      # MediaWiki 1.32+ slot model
            "format":  "json",
        })
        r.raise_for_status()

        pages = r.json()["query"]["pages"]
        # The API returns pages keyed by page ID; -1 means "page not found"
        page = next(iter(pages.values()))

        if "missing" in page:
            return {"title": title, "content": "", "exists": False}

        # Navigate the nested structure to get wikitext
        content = (
            page.get("revisions", [{}])[0]
                .get("slots", {})
                .get("main", {})
                .get("*", "")
        )
        return {"title": page["title"], "content": content, "exists": True}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """
        Full-text search across all wiki pages.

        Returns a list of dicts:
          [{"title": str, "snippet": str, "size": int}, ...]

        The snippet has HTML highlight tags (<span class="searchmatch">)
        which we strip before returning.
        """
        r = self.session.get(self.api_url, params={
            "action":   "query",
            "list":     "search",
            "srsearch": query,
            "srlimit":  limit,
            "srprop":   "snippet|size",
            "format":   "json",
        })
        r.raise_for_status()

        results = []
        for item in r.json().get("query", {}).get("search", []):
            # Strip HTML tags from the snippet — MCP consumers expect plain text
            snippet = item.get("snippet", "")
            snippet = re.sub(r"<[^>]+>", "", snippet)
            results.append({
                "title":   item["title"],
                "snippet": snippet,
                "size":    item.get("size", 0),
            })
        return results

    def list_pages(self, prefix: str = "", namespace: int = 0, limit: int = 20) -> list[str]:
        """
        List page titles, optionally filtered by a title prefix.

        :param prefix:    Only return pages whose title starts with this string
        :param namespace: MediaWiki namespace number (0 = main, 1 = Talk, etc.)
        :param limit:     Max number of results
        """
        params = {
            "action":      "query",
            "list":        "allpages",
            "apnamespace": namespace,
            "aplimit":     limit,
            "format":      "json",
        }
        if prefix:
            params["apprefix"] = prefix

        r = self.session.get(self.api_url, params=params)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("allpages", [])
        return [p["title"] for p in pages]