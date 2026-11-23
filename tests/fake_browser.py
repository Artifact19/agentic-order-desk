"""A stand-in for the Zomato browser so tests run offline and repeatably.

The restaurant cards and menus are written in the same text form Zomato's pages produce, and they go through the
real parsers (parse_card, match_menu), so only the Chrome window itself is replaced.
"""
from app.zomato_browser import BrowserBlocked, match_menu, parse_card

CARDS = [
    ("/riverton/sunrise-snacks-river-road/order", "Sunrise Snacks", "₹140 OFF\nSunrise Snacks\n4.3\nBurger, Fast Food, Street Food\n₹150 for one\n25–30 min"),
    ("/riverton/cheesy-crust-market-street/order", "Cheesy Crust", "₹140 OFF\nCheesy Crust\n4.3\nPizza, Burger, Pasta\n₹400 for one\n25–30 min"),
    ("/riverton/corner-slice-old-town/order", "Corner Slice", "50% OFF\nKNS\n4.3\nPizza, Fast Food\n₹200 for one\n20–25 min"),
    ("/riverton/the-bean-house-green-park/order", "The Bean House", "The Bean House\n4.0\nCafe, Pizza, Beverages\n₹200 for one\n15–20 min"),
    ("/riverton/tea-stop-hill-view/order", "Tea Stop", "Tea Stop\n4.5\nTea, Beverages, Snacks\n₹100 for one\n20–25 min"),
    ("/riverton/stone-oven-lake-road/order", "Stone Oven", "30% OFF\nStone Oven\n4.2\nPizza, Burger\n₹200 for one\n50–55 min"),
    ("/riverton/new-place-east-end/order", "New Place", "New Place\nNEW\nFast Food, Pizza\n₹150 for one\n20–25 min"),
]

MENUS = {
    "sunrise-snacks-river-road": [("Cheese pizza", 140), ("Mix veg pizza", 190), ("Veg cheese pizza", 160),
                                     ("Pizza Base", 40), ("Chicken Pizza", 220), ("Veg Biryani", 180),
                                     ("Chicken Dum Biryani", 260)],
    "cheesy-crust-market-street": [("Margherita Pizza", 199), ("Four Cheese Pizza", 209), ("Paneer Tikka Pizza", 279),
                             ("Veg Biryani Bowl", 230)],
    "corner-slice-old-town": [("Tomatino Pizza", 210), ("Sweet Corn Pizza", 225), ("Masala Dosa", 90)],
    "the-bean-house-green-park": [("Plain cheese pizza", 162), ("Classic Cold Coffee", 120), ("Veg Biryani", 150)],
    "tea-stop-hill-view": [("Cutting Chai", 20), ("Cold Coffee", 90)],
    "stone-oven-lake-road": [("Farmhouse Pizza", 249)],
    "new-place-east-end": [("Corn Pizza", 120)],
}

BILL = {"item_total": "₹140", "delivery_fee": "₹25", "distance_km": None, "savings": "₹34 on delivery",
        "taxes": "₹9.15", "to_pay": "₹180.15", "delivery_time": "25–30 mins", "deliver_to": "Home: Green Park"}


class FakeBrowser:
    attached = True
    channel_used = "fake"

    def __init__(self, ready: bool = True):
        self.ready = ready
        self.calls = []
        self.fail_next = {}  # tool name -> number of BrowserBlocked errors to raise first
        self.prices = {}  # (slug, dish) -> price override, to mimic a price change on Zomato
        self.not_delivering = set()
        self.closed = {}  # slug -> 'Opens at 7pm'
        self.last_status = {}

    def _maybe_fail(self, tool: str):
        if self.fail_next.get(tool):
            self.fail_next[tool] -= 1
            raise BrowserBlocked(f"Zomato did not respond during {tool}")

    def account_status(self, launch: bool = False, refresh: bool = False) -> dict:
        self.calls.append(("account_status",))
        self.last_status = {"browser_running": True, "logged_in": self.ready, "user": "Test",
                            "location": "Home - Green Park" if self.ready else "Riverton", "address_set": self.ready}
        return self.last_status

    def open_for_login(self) -> dict:
        self.calls.append(("open_for_login",))
        return self.account_status()

    def search(self, run_id: str, item: str, is_veg: bool) -> dict:
        self.calls.append(("search", item, is_veg))
        self._maybe_fail("search")
        restaurants = [parse_card({"href": href, "name": name, "text": text}) for href, name, text in CARDS]
        return {"url": f"https://www.zomato.com/riverton/delivery/dish-{item.replace(' ', '-')}",
                "filters": ["Pure Veg"] if is_veg else [], "restaurants": restaurants,
                "actions": ["Opened the dish page", "Read restaurant cards"], "screenshot": ""}

    def read_menu(self, run_id: str, restaurant: dict, item: str, is_veg) -> dict:
        self.calls.append(("read_menu", restaurant["slug"], item))
        self._maybe_fail("read_menu")
        raw = [{"name": name, "prices": [self.prices.get((restaurant["slug"], name), price)]}
               for name, price in MENUS.get(restaurant["slug"], [])]
        delivers = restaurant["slug"] not in self.not_delivering
        closed = self.closed.get(restaurant["slug"], "")
        usable = delivers and not closed
        return {"url": restaurant["url"], "dishes": match_menu(raw, restaurant["slug"], item, is_veg) if usable else [],
                "dishes_on_page": len(raw), "delivers": delivers, "closed": closed, "screenshot": ""}

    def hand_off(self, run_id: str, restaurant_url: str, dish_name: str) -> dict:
        self.calls.append(("hand_off", restaurant_url, dish_name))
        return {"url": restaurant_url + "/verify", "added_to_cart": True, "checkout_open": True, "bill": dict(BILL),
                "note": "Zomato's checkout page is open: ₹180.15 to pay.", "screenshot": ""}

    def close(self):
        pass

    def count(self, tool: str) -> int:
        return sum(1 for call in self.calls if call[0] == tool)
