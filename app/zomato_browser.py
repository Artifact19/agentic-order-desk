"""Browser tools that let the agent use the real Zomato website.

The agent drives one tab in a visible Chrome window: it opens Zomato's delivery listing for a dish, clicks filters,
scrolls, reads the restaurant cards, opens menus and reads dish prices. On approval it adds the dish, opens the
checkout page and reads the bill. It never types credentials, presses Pay or places an order.

Chrome is started as a normal window with its own profile (so a Zomato sign-in persists and the user's everyday
Chrome is untouched) and a DevTools port. The agent talks to one tab over the Chrome DevTools Protocol directly,
which avoids the bot-detection flags that automation launchers add and works with any Chrome version.
"""
import base64
import hashlib
import itertools
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket

BASE_URL = "https://www.zomato.com"

# Zomato's dish-page names where they differ from the everyday name. Any other dish uses its own words,
# and a dish without a page falls back to Zomato's search.
DISH_SLUGS = {"misal": "misal-pav", "wrap": "rolls"}

# Words that also count when matching a requested word against menu dish names.
WORD_SYNONYMS = {"wrap": ["wrap", "roll", "frankie"], "roll": ["roll", "wrap", "frankie"], "momo": ["momo"],
                 "noodle": ["noodle", "chowmein"], "biryani": ["biryani", "biriyani"], "sandwich": ["sandwich", "sub"],
                 "coffee": ["coffee"], "waffle": ["waffle"], "dosa": ["dosa"], "thali": ["thali"]}

NON_VEG_WORDS = re.compile(r"\b(chicken|mutton|egg|fish|prawn|keema|meat|lamb|pepperoni|ham|bacon|tuna|"
                           r"murgh|gosht|salami|sausage|non[- ]?veg)\b", re.I)

# Menu lines that mention the dish but are not a meal on their own.
NOT_A_MEAL = re.compile(r"\b(base|add[- ]?on|extra|topping|toppings|dip|sauce|seasoning|sprinkle|oregano|"
                        r"chilli flakes|crust only|packaging)\b", re.I)

CARDS_JS ="""(() => [...document.querySelectorAll('.jumbo-tracker')].map(card => {
  const link = card.querySelector('a[href*="/order"]');
  const title = card.querySelector('h4');
  return link && title ? {href: link.getAttribute('href'), name: title.innerText.trim(), text: card.innerText} : null;
}).filter(Boolean))()"""

MENU_JS = """(() => {
  const out = [];
  for (const h of document.querySelectorAll('h4')) {
    let box = h;
    for (let i = 0; i < 5 && box.parentElement; i++) {
      box = box.parentElement;
      if (/₹\\s?\\d/.test(box.innerText)) break;
    }
    if (box.querySelectorAll('h4').length !== 1) continue;
    const prices = [...box.innerText.matchAll(/₹\\s?([\\d,]+)/g)].map(m => parseInt(m[1].replace(/,/g, ''), 10));
    if (!prices.length) continue;
    out.push({name: h.innerText.trim(), prices});
  }
  return out;
})()"""

CLICK_PURE_VEG_JS = """(() => {
  const pill = [...document.querySelectorAll('.pill_meta_filter')].find(e => e.innerText.trim() === 'Pure Veg');
  if (!pill) return false;
  pill.scrollIntoView({block: 'center'}); pill.click(); return true;
})()"""

HIGHLIGHT_AND_ADD_JS = """((name) => {
  const h = [...document.querySelectorAll('h4')].find(e => e.innerText.trim() === name);
  if (!h) return 'not-found';
  let box = h;
  for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; if (/\\bADD\\b/.test(box.innerText)) break; }
  box.style.outline = '4px solid #e23744'; box.style.borderRadius = '10px'; box.style.outlineOffset = '4px';
  box.scrollIntoView({block: 'center'});
  const add = [...box.querySelectorAll('*')].find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === 'ADD');
  if (!add) return /(^|\\n)\\s*\\d+\\s*(\\n|$)/.test(box.innerText) ? 'already-in-cart' : 'no-add-button';
  (add.closest('button') || add.parentElement || add).click();
  return 'clicked';
})"""


ACCOUNT_JS = """(() => {
  const inputs = [...document.querySelectorAll('input')];
  const loc = inputs.find(i => !/search for restaurant/i.test(i.placeholder || '') && i.offsetParent !== null
                               && i.getBoundingClientRect().top < 120);
  if (!loc) return {loaded: false};
  const top = [...document.querySelectorAll('a, span, div, p, button')].filter(e => e.children.length === 0
      && e.offsetParent !== null && e.getBoundingClientRect().top < 120 && (e.innerText || '').trim());
  const auth = top.filter(e => /^(log ?in|sign ?up)$/i.test(e.innerText.trim()));
  const right = top.filter(e => e.getBoundingClientRect().left > loc.getBoundingClientRect().right + 200)
                   .map(e => e.innerText.trim()).filter(t => t.length < 40);
  return {loaded: true, logged_in: auth.length === 0, location: (loc.value || loc.placeholder || '').trim(),
          user: auth.length ? '' : (right[0] || ''), login_modal: /enter your phone|send one time password|login with email/i.test(document.body.innerText)};
})()"""

CLICK_LOGIN_JS = """(() => {
  const link = [...document.querySelectorAll('a, span, div, p, button')].find(e => e.children.length === 0
      && e.getBoundingClientRect().top < 120 && /^log ?in$/i.test((e.innerText || '').trim()));
  if (!link) return false;
  link.click(); return true;
})()"""


# Saved screenshots blur the customer's name, phone numbers and delivery address (only while the picture is taken).
PRIVACY_BLUR_JS = r"""(() => {
  if (!document.getElementById('agent-privacy')) {
    const style = document.createElement('style');
    style.id = 'agent-privacy';
    style.textContent = '.agent-blur { filter: blur(7px) !important; }';
    document.head.appendChild(style);
  }
  const mark = (e) => e && e.classList.add('agent-blur');
  for (const input of document.querySelectorAll('input')) {
    if (input.getBoundingClientRect().top < 120 && !/search for restaurant/i.test(input.placeholder || '')) mark(input);
  }
  for (const e of document.querySelectorAll('span, div, p, a, h4, h5, h6, button')) {
    const text = (e.innerText || '').trim();
    const r = e.getBoundingClientRect();
    if (!text || r.height > 140) continue;
    if (r.height < 90 && /\+91[-\s]?\d{6,}|\b\d{10}\b/.test(text)) mark(e);  // name + phone lines, nested or not
    if (/^Delivery at\b/.test(text)) mark(e);
    if (e.children.length === 0 && r.top < 100 && r.left > window.innerWidth * 0.7 && text.length < 30
        && !/\d/.test(text) && !/rating/i.test(text)) mark(e);  // the signed-in name in the header
  }
  return true;
})()"""
PRIVACY_CLEAR_JS = "document.querySelectorAll('.agent-blur').forEach(e => e.classList.remove('agent-blur'))"


class ZomatoNotReady(Exception):
    """The user has not signed in to Zomato or set a delivery address yet (not retried automatically)."""


class BrowserBlocked(ConnectionError):
    """Zomato or Chrome did not respond as expected. Retryable, and the run can be resumed."""


def dish_slug(item: str) -> str:
    return DISH_SLUGS.get(item, re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-"))


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def keywords_for(item: str) -> list:
    """One list of acceptable spellings per word of the dish; every word must match a menu dish name."""
    words = [_singular(w) for w in re.findall(r"[a-z]+", item.lower())]
    return [WORD_SYNONYMS.get(w, [w]) for w in words] or [[item.lower()]]


def dish_matches(name: str, item: str) -> bool:
    lowered = name.lower()
    return all(any(option in lowered for option in options) for options in keywords_for(item))


def match_menu(raw: list, restaurant_slug: str, item: str, is_veg) -> list:
    """Keep the menu dishes that match the request: every word of the dish, the diet, and no add-ons."""
    dishes, seen = [], set()
    for dish in raw:
        name = dish["name"]
        lowered = name.lower()
        if lowered in seen or not dish_matches(name, item) or NOT_A_MEAL.search(name):
            continue
        if is_veg is True and NON_VEG_WORDS.search(name):
            continue
        if is_veg is False and not NON_VEG_WORDS.search(name):
            continue
        seen.add(lowered)
        item_id = "zm-" + hashlib.sha1(f"{restaurant_slug}|{lowered}".encode()).hexdigest()[:10]
        dishes.append({"item_id": item_id, "name": name, "price": min(dish["prices"])})
    return dishes


def parse_card(card: dict) -> dict:
    lines = [ln.strip() for ln in card["text"].split("\n") if ln.strip()]
    offer = next((ln for ln in lines if "OFF" in ln.upper()), "")
    rating = next((float(ln) for ln in lines if re.fullmatch(r"[1-5]\.\d", ln)), None)
    cost = next((int(m.group(1).replace(",", "")) for ln in lines
                 if (m := re.search(r"₹\s?([\d,]+)\s*for\s*(?:one|two)", ln))), None)
    eta = next(((int(m.group(1)), int(m.group(2))) for ln in lines
                if (m := re.search(r"(\d+)\s*[–-]\s*(\d+)\s*min", ln))), None)
    single_eta = next((int(m.group(1)) for ln in lines if (m := re.fullmatch(r"(\d+)\s*min", ln))), None)
    skip = {card["name"], offer} | {ln for ln in lines if re.fullmatch(r"[1-5]\.\d", ln) or "₹" in ln or "min" in ln}
    cuisines = next((ln for ln in lines if ln not in skip and "," in ln), "")
    path = card["href"].split("?")[0].strip("/").split("/")
    slug = path[1] if len(path) >= 2 else re.sub(r"[^a-z0-9]+", "-", card["name"].lower())
    eta_low, eta_high = eta if eta else (single_eta, single_eta)
    return {"restaurant_id": f"zomato:{slug}", "slug": slug, "name": card["name"], "rating": rating,
            "cuisines": cuisines, "cost_for_one": cost, "eta_low": eta_low, "eta_high": eta_high,
            "offer": offer, "url": BASE_URL + card["href"].split("?")[0], "listing_href": BASE_URL + card["href"]}


def _money(pattern: str, text: str):
    m = re.search(pattern, text, re.I)
    return f"₹{m.group(1)}" if m else None


def parse_bill(text: str) -> dict:
    """Read the bill on Zomato's checkout page (/order/verify)."""
    # Discounted fees appear as "₹59 ₹25" (struck-out original first), so take the last amount in the block.
    fee_block = re.search(r"Delivery (?:partner )?fee(?: for ([\d.]+) km)?([\s\S]{0,160}?)(?=Platform fee|Feeding|GST|To pay)",
                          text, re.I)
    fee_amounts = re.findall(r"₹\s?([\d,.]+)", fee_block.group(2)) if fee_block else []
    saved = re.search(r"You saved ₹\s?([\d,.]+)([^\n]*)", text)
    eta = re.search(r"Delivery in ([^\n]+)", text)
    address = re.search(r"Delivery at ([^\n]+)\n([^\n]+)", text)
    return {"item_total": _money(r"Item total\s*₹\s?([\d,.]+)", text),
            "delivery_fee": f"₹{fee_amounts[-1]}" if fee_amounts else None,
            "distance_km": float(fee_block.group(1)) if fee_block and fee_block.group(1) else None,
            "savings": f"₹{saved.group(1)}{saved.group(2).rstrip()}" if saved else None,
            "taxes": _money(r"GST[^\n]*\s*₹\s?([\d,.]+)", text),
            "to_pay": _money(r"To pay\s*₹\s?([\d,.]+)", text),
            "delivery_time": eta.group(1).strip() if eta else None,
            "deliver_to": f"{address.group(1).strip()}: {address.group(2).strip()[:80]}" if address else None}


class CDPTab:
    """A single Chrome tab driven over the DevTools Protocol."""

    def __init__(self, endpoint: str, target: dict):
        self.endpoint = endpoint
        self.target_id = target["id"]
        self._ids = itertools.count(1)
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)

    @classmethod
    def open(cls, endpoint: str):
        request = urllib.request.Request(f"{endpoint}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(request, timeout=10) as response:
            return cls(endpoint, json.load(response))

    def send(self, method: str, params=None, timeout: float = 60):
        message_id = next(self._ids)
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.5, deadline - time.time()))
            reply = json.loads(self.ws.recv())
            if reply.get("id") == message_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})
        raise TimeoutError(f"{method} timed out")

    def evaluate(self, expression: str, timeout: float = 30):
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": True}, timeout)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("text", "script error"))
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 15, interval: float = 0.3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                value = self.evaluate(expression)
                if value:
                    return value
            except RuntimeError:
                pass
            time.sleep(interval)
        return None

    def navigate(self, url: str, timeout: float = 45):
        result = self.send("Page.navigate", {"url": url}, timeout)
        if result.get("errorText"):
            raise BrowserBlocked(f"Could not open {url}: {result['errorText']}")
        if not self.wait_for("document.readyState !== 'loading' && location.href !== 'about:blank'", timeout):
            raise BrowserBlocked(f"{url} did not finish loading within {timeout:g}s")

    def screenshot(self, path: Path):
        data = self.send("Page.captureScreenshot", {"format": "png"}, 30)["data"]
        path.write_bytes(base64.b64decode(data))

    def click_at(self, x: float, y: float):
        """A real mouse click (some Zomato buttons ignore element.click())."""
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    def click_text(self, text: str) -> bool:
        rect = self.evaluate(f"""(() => {{
            const el = [...document.querySelectorAll('button, a, div, span')].filter(e => (e.innerText || '').trim() === {json.dumps(text)}
                && e.offsetParent !== null).pop();
            if (!el) return null;
            el.scrollIntoView({{block: 'center'}});
            const r = el.getBoundingClientRect(); return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
        }})()""")
        if not rect:
            return False
        time.sleep(0.3)
        self.click_at(rect["x"], rect["y"])
        return True

    def bring_to_front(self):
        self.send("Page.bringToFront")

    def alive(self) -> bool:
        try:
            return self.evaluate("1") == 1
        except Exception:
            return False

    def close(self, close_tab: bool = False):
        try:
            self.ws.close()
        except Exception:
            pass
        if close_tab:
            try:
                urllib.request.urlopen(f"{self.endpoint}/json/close/{self.target_id}", timeout=5).close()
            except Exception:
                pass


class ZomatoBrowser:
    def __init__(self, profile_dir: Path, screens_dir: Path, city: str = "", max_restaurants: int = 6,
                 port: int = 9333, chrome_paths=(r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")):
        self.profile_dir = Path(profile_dir)
        self.screens_dir = Path(screens_dir)
        # Zomato's URLs carry a city. Unless one is configured, it is learned from Zomato itself: /delivery
        # redirects to the city of the signed-in account's delivery address.
        self.city = (city or "").strip().lower()
        self.max_restaurants = max_restaurants
        self.port = port
        self.endpoint = f"http://127.0.0.1:{port}"
        self.chrome_paths = list(chrome_paths)
        self.channel_used = None
        self._tab = None
        self._lock = threading.RLock()  # one DevTools socket, used by the agent thread and status requests
        self.last_status = {"browser_running": False}

    # ---------- lifecycle ----------
    @property
    def attached(self) -> bool:
        return self._tab is not None

    def _endpoint_alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.endpoint}/json/version", timeout=2) as response:
                self.channel_used = json.load(response).get("Browser")
            return True
        except Exception:
            return False

    def _ensure_chrome(self):
        """Start the agent's Chrome window if it is not already running.

        QUIC is disabled because Zomato's CDN stalls over HTTP/3 from this network (36 s to first byte vs 5 s).
        """
        if self._endpoint_alive():
            return
        executable = next((p for p in self.chrome_paths if Path(p).exists()), None)
        if executable is None:
            raise RuntimeError("Google Chrome or Microsoft Edge was not found")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([executable, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile_dir}",
                          "--disable-quic", "--no-first-run", "--no-default-browser-check", "--start-maximized",
                          # keep working (and painting) when the window is behind other windows
                          "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                          "--disable-background-timer-throttling",
                          self._home_url()])
        for _ in range(60):
            if self._endpoint_alive():
                return
            time.sleep(0.25)
        raise BrowserBlocked("Chrome started but its DevTools port did not open")

    def _home_url(self) -> str:
        return f"{BASE_URL}/{self.city}/delivery" if self.city else f"{BASE_URL}/delivery"

    def _learn_city(self, tab) -> None:
        """Remember the city Zomato redirected to (zomato.com/<city>/delivery)."""
        if self.city:
            return
        match = re.match(r"https://www\.zomato\.com/([a-z0-9-]+)/delivery\b", tab.evaluate("location.href") or "")
        if match:
            self.city = match.group(1)

    def _page(self) -> CDPTab:
        if self._tab is not None and self._tab.alive():
            return self._tab
        if self._tab is not None:
            self._tab.close()
        self._ensure_chrome()
        try:
            self._tab = self._adopt_tab() or CDPTab.open(self.endpoint)
            self._tab.send("Page.enable")
            # Let the page follow the real window size (a fixed override left a grey area when maximised and
            # hid Zomato's cart bar). The window opens maximised, which gives Zomato's desktop layout.
            self._tab.send("Emulation.clearDeviceMetricsOverride")
        except Exception as error:
            self._tab = None
            raise BrowserBlocked(f"Could not open a tab in the agent's Chrome window: {error}") from error
        return self._tab

    def _adopt_tab(self):
        """Reuse the window's Zomato or blank tab, so sign-in and searching happen in one tab."""
        try:
            with urllib.request.urlopen(f"{self.endpoint}/json/list", timeout=5) as response:
                targets = [t for t in json.load(response) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        except Exception:
            return None
        for wanted in ("zomato.com", "about:blank", "chrome://newtab", "chrome://new-tab-page"):
            for target in targets:
                if wanted in target.get("url", ""):
                    try:
                        return CDPTab(self.endpoint, target)
                    except Exception:
                        continue
        return None

    # ---------- account ----------
    def account_status(self, launch: bool = False, refresh: bool = False) -> dict:
        """Is the user signed in to Zomato, and which delivery location is set? Never steals focus."""
        if not self._lock.acquire(timeout=0.5 if not launch else 30):
            return {**self.last_status, "busy": True}
        try:
            if not launch and not self._endpoint_alive():
                self._tab = None
                self.last_status = {"browser_running": False, "logged_in": False, "location": "", "address_set": False}
                return self.last_status
            tab = self._page()
            here = tab.evaluate("location.href") or ""
            loaded = f"(() => {{ const r = {ACCOUNT_JS}; return r.loaded ? r : null; }})()"
            if "zomato.com" not in here:
                tab.navigate(self._home_url())
            elif refresh and not (tab.evaluate(ACCOUNT_JS) or {}).get("login_modal"):
                tab.navigate(here)
            info = tab.wait_for(loaded, timeout=3) or {}
            if not info:
                # Pages such as checkout or a restaurant menu have no address box in the header. Status polls keep
                # the last confirmed answer instead of moving the tab (the customer may be paying); the agent's own
                # check before a search goes back to Zomato's home page to look again.
                if not launch and self.last_status.get("browser_running") and "logged_in" in self.last_status:
                    return {**self.last_status, "page": here}
                tab.navigate(self._home_url())
                info = tab.wait_for(loaded, timeout=15) or {}
            location = info.get("location", "")
            self._learn_city(tab)
            # Before an address is chosen, Zomato's header shows only the city name.
            city_only = location.strip().lower() == self.city.replace("-", " ") if self.city else "," not in location
            self.last_status = {"browser_running": True, "logged_in": bool(info.get("logged_in")),
                                "user": info.get("user", ""), "location": location,
                                "address_set": bool(location) and not city_only,
                                "login_modal_open": bool(info.get("login_modal")), "page": here}
            return self.last_status
        except Exception as error:
            self.last_status = {"browser_running": self._endpoint_alive(), "logged_in": False, "location": "",
                                "address_set": False, "error": f"{type(error).__name__}: {error}"}
            return self.last_status
        finally:
            self._lock.release()

    def open_for_login(self) -> dict:
        """Bring the agent's window forward on Zomato and open the login form if the user is signed out."""
        with self._lock:
            status = self.account_status(launch=True)
            tab = self._page()
            tab.bring_to_front()
            if not status.get("logged_in") and not status.get("login_modal_open"):
                tab.evaluate(CLICK_LOGIN_JS)
            return status

    def close(self):
        """Disconnect. The Chrome window and the agent's tab stay open for the user."""
        if self._tab is not None:
            self._tab.close()
        self._tab = None

    def _shot(self, run_id: str, name: str) -> str:
        folder = self.screens_dir / run_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{re.sub(r'[^a-z0-9_-]+', '_', name.lower())}.png"
        try:
            self._tab.evaluate(PRIVACY_BLUR_JS)
            self._tab.screenshot(path)
        except Exception:
            return ""
        finally:
            try:
                self._tab.evaluate(PRIVACY_CLEAR_JS)
            except Exception:
                pass
        return f"/screens/{run_id}/{path.name}"

    def _restore_window(self):
        """Chrome stops drawing a minimized window, which stalls screenshots and clicks: bring it back first."""
        try:
            with urllib.request.urlopen(f"{self.endpoint}/json/version", timeout=3) as response:
                browser_ws = json.load(response)["webSocketDebuggerUrl"]
            ws = websocket.create_connection(browser_ws, timeout=5, suppress_origin=True)
            try:
                ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget",
                                    "params": {"targetId": self._tab.target_id}}))
                window = json.loads(ws.recv()).get("result", {})
                if window.get("bounds", {}).get("windowState") == "minimized":
                    ws.send(json.dumps({"id": 2, "method": "Browser.setWindowBounds",
                                        "params": {"windowId": window["windowId"], "bounds": {"windowState": "normal"}}}))
                    ws.recv()
            finally:
                ws.close()
        except Exception:
            pass  # cosmetic: the run continues even if the window cannot be restored

    def _goto(self, url: str) -> CDPTab:
        tab = self._page()
        self._restore_window()
        try:
            tab.navigate(url)
        except (TimeoutError, websocket.WebSocketException) as error:
            self.close()
            raise BrowserBlocked(f"Could not open {url}: {error}") from error
        title = (tab.evaluate("document.title") or "").lower()
        if "access denied" in title or "request blocked" in title:
            raise BrowserBlocked(f"Zomato refused the page ({title})")
        return tab

    # ---------- tools ----------
    def search(self, run_id: str, item: str, is_veg: bool) -> dict:
        with self._lock:
            return self._search(run_id, item, is_veg)

    def _type_in_search_box(self, tab, item: str, run_id: str = "") -> str:
        """Type the dish into Zomato's search box and click its 'Dish' suggestion, as a person would.

        Returns the suggestion that was chosen, or '' when Zomato suggests no dish of that name."""
        box = "document.querySelector('input[placeholder*=\"Search for restaurant\"]')"
        if not tab.wait_for(box + " !== null", timeout=15):
            return ""
        time.sleep(1.5)  # let the page hydrate so the box reacts to typing
        rect = tab.evaluate(f"""(() => {{ const i = {box}; i.scrollIntoView({{block: 'center'}});
            const r = i.getBoundingClientRect(); return {{x: r.x + 40, y: r.y + r.height / 2}}; }})()""")
        tab.click_at(rect["x"], rect["y"])
        time.sleep(0.4)
        tab.send("Input.insertText", {"text": item})
        find = r"""((wanted) => {
          // one row per suggestion: its text is "<Name> - Delivery", then "Dish"
          const rows = [...document.querySelectorAll('div, a, p, section')].filter(e => {
            const t = (e.innerText || '').trim();
            return /^[^\n]+ - Delivery\s*\n+\s*Dish\s*$/.test(t) && e.getBoundingClientRect().height < 160;
          }).map(e => { const r = e.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2, text: e.innerText.split('\n')[0].trim()}; });
          if (!rows.length) return null;
          const exact = rows.find(r => r.text.toLowerCase() === wanted + ' - delivery');
          return exact || rows.sort((a, b) => a.y - b.y)[0];
        })(""" + json.dumps(item.lower()) + ")"
        suggestion = tab.wait_for(find, timeout=8)
        if not suggestion:
            return ""
        if run_id:
            self._shot(run_id, "search_typed")
        tab.click_at(suggestion["x"], suggestion["y"])
        return suggestion["text"]

    def _search(self, run_id: str, item: str, is_veg: bool) -> dict:
        """Search Zomato for the dish near the delivery address, apply filters, scroll and read the cards."""
        cards_ready = "document.querySelectorAll('.jumbo-tracker h4').length"
        tab = self._goto(self._home_url())
        self._learn_city(tab)
        tab.bring_to_front()
        chosen = self._type_in_search_box(tab, item, run_id)
        actions = [f"Typed '{item}' into Zomato's search box"]
        if chosen and tab.wait_for(cards_ready, timeout=15):
            actions.append(f"Chose the suggestion '{chosen}'")
        elif self.city:
            url = f"{BASE_URL}/{self.city}/delivery/dish-{dish_slug(item)}"
            tab = self._goto(url)
            actions.append(f"Zomato suggested no dish called '{item}'; opened {url}")
            if "404" in (tab.evaluate("document.title") or "") or not tab.wait_for(cards_ready, timeout=12):
                actions.append(f"Zomato has no listing for '{item}'")
                return {"url": tab.evaluate("location.href"), "filters": [], "restaurants": [], "actions": actions,
                        "screenshot": self._shot(run_id, "search")}
        else:
            actions.append(f"Zomato suggested no dish called '{item}'")
            return {"url": tab.evaluate("location.href"), "filters": [], "restaurants": [], "actions": actions,
                    "screenshot": self._shot(run_id, "search")}
        time.sleep(1.0)
        filters = []
        if is_veg:
            for _ in range(5):  # the page is server-rendered; clicks before React hydrates are ignored
                if tab.evaluate(CLICK_PURE_VEG_JS) and tab.wait_for("location.search.includes('meta_filter')", 3):
                    break
                time.sleep(1.0)
            if "meta_filter" in (tab.evaluate("location.search") or ""):
                tab.wait_for("document.querySelectorAll('.jumbo-tracker h4').length", timeout=10)
                time.sleep(1.0)
                filters.append("Pure Veg")
                actions.append("Clicked the 'Pure Veg' filter")
            else:
                actions.append("Pure Veg filter did not respond; veg dishes are filtered by name instead")
        cards, last, stalls = [], -1, 0
        for _ in range(12):
            cards = tab.evaluate(CARDS_JS) or []
            if len(cards) >= 18:
                break
            stalls = stalls + 1 if len(cards) == last else 0
            if stalls >= 2:
                break
            last = len(cards)
            tab.evaluate("window.scrollBy(0, 1400)")
            time.sleep(0.9)
        actions.append(f"Scrolled the listing and read {len(cards)} restaurant cards")
        tab.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.4)
        restaurants, seen = [], set()
        for card in cards:
            parsed = parse_card(card)
            if parsed["slug"] in seen or parsed["eta_high"] is None:
                continue
            seen.add(parsed["slug"])
            restaurants.append(parsed)
        return {"url": tab.evaluate("location.href"), "filters": filters, "restaurants": restaurants,
                "actions": actions, "screenshot": self._shot(run_id, "search")}

    def read_menu(self, run_id: str, restaurant: dict, item: str, is_veg) -> dict:
        with self._lock:
            return self._read_menu(run_id, restaurant, item, is_veg)

    def _read_menu(self, run_id: str, restaurant: dict, item: str, is_veg) -> dict:
        """Open a restaurant's online menu and return the dishes that match the request."""
        tab = self._goto(restaurant["listing_href"])
        if not tab.wait_for("document.querySelectorAll('h4').length > 2 && /₹\\s?\\d/.test(document.body.innerText)",
                            timeout=20):
            raise BrowserBlocked(f"The menu for {restaurant['name']} did not load")
        time.sleep(1.2)
        raw = tab.evaluate(MENU_JS) or []
        # Zomato shows "Does not deliver to: <address>" for restaurants outside the delivery area.
        not_delivering = bool(tab.evaluate("/Does not deliver to/.test(document.body.innerText)"))
        if not_delivering:
            time.sleep(2.0)  # the banner can flash while the saved address loads; check again
            not_delivering = bool(tab.evaluate("/Does not deliver to/.test(document.body.innerText)"))
        dishes = match_menu(raw, restaurant["slug"], item, is_veg)
        # A closed restaurant shows "Closed" and "Opens at 7pm"; its cart cannot be changed until it opens.
        closed_note = tab.evaluate(r"""(() => { const lines = document.body.innerText.split(/\n/).map(l => l.trim());
            if (lines.includes('Open now')) return '';
            const opens = lines.find(l => /^Opens (at|in|tomorrow)/i.test(l));
            return lines.includes('Closed') || /currently closed/i.test(document.body.innerText) ? (opens || 'Closed') : ''; })()""") or ""
        if dishes:
            tab.evaluate(f"""(() => {{ const h = [...document.querySelectorAll('h4')]
                .find(e => e.innerText.trim() === {json.dumps(dishes[0]['name'])});
                if (h) h.scrollIntoView({{block: 'center'}}); }})()""")
            time.sleep(0.4)
        return {"url": tab.evaluate("location.href"), "dishes": [] if not_delivering or closed_note else dishes,
                "dishes_on_page": len(raw), "delivers": not not_delivering, "closed": closed_note,
                "screenshot": self._shot(run_id, f"menu_{restaurant['slug']}")}

    def hand_off(self, run_id: str, restaurant_url: str, dish_name: str) -> dict:
        with self._lock:
            return self._hand_off(run_id, restaurant_url, dish_name)

    def _hand_off(self, run_id: str, restaurant_url: str, dish_name: str) -> dict:
        """Add the chosen dish, open Zomato's checkout page and read the bill. Never places the order or pays."""
        tab = self._goto(restaurant_url)
        tab.bring_to_front()
        result = {"url": restaurant_url, "added_to_cart": False, "checkout_open": False, "bill": None}
        tab.wait_for("document.querySelectorAll('h4').length > 2", timeout=20)
        time.sleep(1.0)
        outcome = tab.evaluate(f"({HIGHLIGHT_AND_ADD_JS})({json.dumps(dish_name)})")
        time.sleep(2.0)
        body = tab.evaluate("document.body.innerText") or ""
        in_cart = re.search(r"Your Order \((\d+)\)", body)
        if outcome == "not-found":
            result["note"] = "Opened the restaurant page, but the dish was not visible. Search the menu for it."
        elif re.search(r"replace cart|items? already in (?:your )?cart|start afresh|clear cart", body, re.I):
            result["note"] = ("Your Zomato cart has items from another restaurant. Zomato is asking whether to replace "
                              "them: choose in the Chrome window, then press Continue.")
        elif re.search(r"Enter your phone|Send One Time Password", body, re.I):
            result["note"] = "Zomato asked you to log in. Log in in the Chrome window, then tap ADD on the highlighted dish."
        elif in_cart and int(in_cart.group(1)) > 0:
            result["added_to_cart"] = True
        elif re.search(r"customi[sz]e|choice of|select size|add item", body, re.I):
            result["note"] = "Zomato opened the customisation options. Choose them in the Chrome window, then press Continue."
        else:
            result["note"] = "Highlighted the dish. Tap ADD and then Continue in the Chrome window."
        if result["added_to_cart"]:
            at_checkout = "location.pathname.endsWith('/order/verify')"
            tab.wait_for(r"/Your Order \(\d+\)/.test(document.body.innerText)", timeout=10)
            reached = False
            for _ in range(2):  # the cart bar's Continue button sometimes ignores the first click
                if tab.click_text("Continue") and tab.wait_for(at_checkout, timeout=8):
                    reached = True
                    break
                time.sleep(1.0)
            if not reached and restaurant_url.rstrip("/").endswith("/order"):
                tab.navigate(restaurant_url.rstrip("/") + "/verify")  # the same page Continue opens
                reached = bool(tab.wait_for(at_checkout + " && /To pay/.test(document.body.innerText)", timeout=15))
            if reached:
                tab.wait_for("/To pay/.test(document.body.innerText)", timeout=15)
                time.sleep(1.0)
                result["checkout_open"] = True
                result["url"] = tab.evaluate("location.href")
                result["bill"] = parse_bill(tab.evaluate("document.body.innerText") or "")
                tab.evaluate("window.scrollTo(0, 0)")
                bill = result["bill"]
                result["note"] = ("Zomato's checkout page is open" + (f": {bill['to_pay']} to pay" if bill.get("to_pay") else "")
                                  + ". Check the address, apply a coupon if you like, and pay in the Chrome window.")
            else:
                result["note"] = (f"Added to your Zomato order ({in_cart.group(1)} item(s)). "
                                  "Press Continue in the Chrome window to open checkout.")
        result["screenshot"] = self._shot(run_id, "handoff")
        return result
