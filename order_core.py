"""Deterministic core of the voice ordering agent.

The language model only talks to the customer and chooses which tool to call. Everything that has to be
right is code: menu lookup, quantities, prices and totals, opening hours, and the rule that an order can
only be placed after the customer confirmed a read-back of its *current* contents."""
import json
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_QTY = 20


def load_menu(path=None):
    with open(path or os.path.join(HERE, "menu.json")) as f:
        return json.load(f)


def money(cents):
    return f"${cents / 100:.2f}"


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass
class Line:
    item_id: str
    variant: str | None
    qty: int
    notes: str = ""


@dataclass
class OrderSession:
    menu: dict
    lines: list = field(default_factory=list)
    fulfilment: str | None = None          # "pickup" | "delivery"
    address: str = ""
    customer_name: str = ""
    version: int = 0                       # bumps on every change to the order
    customer_turns: int = 0                # how many times the customer has spoken (set by the caller)
    read_back_turn: int = -1               # customer_turns at the time of the last read-back
    read_back_version: int = -1
    confirmed: bool = False
    placed: bool = False
    transfers: list = field(default_factory=list)
    reservations: list = field(default_factory=list)
    log: list = field(default_factory=list)

    # ---- helpers -------------------------------------------------------------
    def _item(self, item_id):
        for it in self.menu["items"]:
            if it["id"] == item_id:
                return it
        return None

    def _price(self, line):
        it = self._item(line.item_id)
        return it["variants"][line.variant] if "variants" in it else it["price_cents"]

    def note_customer_turn(self):
        """The caller (voice bot or simulator) calls this each time the customer finishes speaking."""
        self.customer_turns += 1

    def _touch(self):
        self.version += 1
        self.confirmed = False

    def subtotal_cents(self):
        return sum(self._price(l) * l.qty for l in self.lines)

    def total_cents(self):
        fee = self.menu["delivery_fee_cents"] if self.fulfilment == "delivery" else 0
        return self.subtotal_cents() + fee

    def _ok(self, **kw):
        return {"ok": True, **kw}

    def _err(self, msg, **kw):
        return {"ok": False, "error": msg, **kw}

    # ---- tools ---------------------------------------------------------------
    def lookup_item(self, item_id):
        it = self._item(item_id)
        if not it:
            return self._err("no such item", available=[i["id"] for i in self.menu["items"]])
        price = it.get("variants") or it["price_cents"]
        return self._ok(id=it["id"], name=it["name"], price=price, allergens_listed=it["allergens"],
                        note="Listed allergens only; kitchen staff must confirm anything about allergies.")

    def add_item(self, item_id, quantity, variant=None, notes=""):
        it = self._item(item_id)
        if not it:
            return self._err("that item is not on the menu", available=[i["id"] for i in self.menu["items"]])
        if not isinstance(quantity, int) or not 1 <= quantity <= MAX_QTY:
            return self._err(f"quantity must be a whole number from 1 to {MAX_QTY}")
        if "variants" in it:
            if variant not in it["variants"]:
                return self._err("this item needs a size", choices=list(it["variants"]))
        else:
            variant = None
        for l in self.lines:                       # same item, same variant, same notes: merge
            if (l.item_id, l.variant, l.notes) == (item_id, variant, notes or ""):
                l.qty += quantity
                break
        else:
            self.lines.append(Line(item_id, variant, quantity, notes or ""))
        self._touch()
        return self._ok(order=self.describe())

    def set_quantity(self, item_id, quantity, variant=None):
        """Change an existing line to an exact quantity (0 removes it)."""
        if not isinstance(quantity, int) or not 0 <= quantity <= MAX_QTY:
            return self._err(f"quantity must be a whole number from 0 to {MAX_QTY}")
        matches = [l for l in self.lines if l.item_id == item_id and (variant is None or l.variant == variant)]
        if not matches:
            return self._err("that item is not in the order")
        if len(matches) > 1:
            return self._err("several lines match; say which size", choices=[l.variant for l in matches])
        if quantity == 0:
            self.lines.remove(matches[0])
        else:
            matches[0].qty = quantity
        self._touch()
        return self._ok(order=self.describe())

    def set_fulfilment(self, method, address="", name=""):
        if method not in ("pickup", "delivery"):
            return self._err("method must be pickup or delivery")
        if method == "delivery" and not address.strip():
            return self._err("delivery needs an address")
        self.fulfilment, self.address = method, address.strip()
        if name.strip():
            self.customer_name = name.strip()
        self._touch()
        return self._ok(order=self.describe())

    def read_back(self):
        """Returns the exact text to read to the customer, and records which version was read."""
        if not self.lines:
            return self._err("the order is empty")
        if not self.fulfilment:
            return self._err("ask whether this is pickup or delivery first")
        if not self.customer_name:
            return self._err("ask for the customer's name first")
        if self.fulfilment == "delivery" and self.subtotal_cents() < self.menu["delivery_minimum_cents"]:
            return self._err(f"delivery needs at least {money(self.menu['delivery_minimum_cents'])} of food",
                             subtotal=money(self.subtotal_cents()))
        self.read_back_version = self.version
        self.read_back_turn = self.customer_turns
        return self._ok(read_back=self.describe(), total=money(self.total_cents()))

    def confirm_order(self):
        """Call only after the customer said yes to the latest read-back."""
        if self.read_back_version != self.version:
            return self._err("the order changed since the last read-back; read it back again first")
        if self.customer_turns <= self.read_back_turn:
            return self._err("the customer has not answered since the read-back; read it to them and wait for their answer")
        self.confirmed = True
        return self._ok()

    def place_order(self):
        if self.placed:
            return self._err("already placed")
        if not self.confirmed or self.read_back_version != self.version:
            return self._err("the customer has not confirmed the current order")
        self.placed = True
        return self._ok(total=money(self.total_cents()), message="Order sent to the kitchen.")

    def book_table(self, party_size, time_hhmm, name):
        o, c = _minutes(self.menu["hours"]["open"]), _minutes(self.menu["hours"]["close"])
        try:
            t = _minutes(time_hhmm)
        except (ValueError, AttributeError):
            return self._err("time must be HH:MM, 24-hour")
        if not isinstance(party_size, int) or not 1 <= party_size <= self.menu["max_party_size"]:
            return self._err(f"we seat 1 to {self.menu['max_party_size']} people online; larger groups go to staff")
        if not o <= t <= c - 60:
            return self._err(f"we are open {self.menu['hours']['open']} to {self.menu['hours']['close']}; "
                             f"the last table is one hour before closing")
        if not name or not name.strip():
            return self._err("a name is needed")
        self.reservations.append({"party": party_size, "time": time_hhmm, "name": name.strip()})
        return self._ok(booked=self.reservations[-1])

    def transfer_to_human(self, reason):
        self.transfers.append(reason)
        return self._ok(message="Transferring to staff.")

    # ---- views ---------------------------------------------------------------
    def describe(self):
        lines = []
        for l in self.lines:
            it = self._item(l.item_id)
            label = f"{l.qty} x {it['name']}" + (f" ({l.variant})" if l.variant else "")
            if l.notes:
                label += f", {l.notes}"
            lines.append(f"{label}: {money(self._price(l) * l.qty)}")
        out = {"lines": lines, "subtotal": money(self.subtotal_cents()),
               "fulfilment": self.fulfilment, "name": self.customer_name, "total": money(self.total_cents())}
        if self.fulfilment == "delivery":
            out["address"], out["delivery_fee"] = self.address, money(self.menu["delivery_fee_cents"])
        return out

    def summary(self):
        """Structured call summary built from state, not from model text."""
        outcome = ("order_placed" if self.placed else "transferred_to_staff" if self.transfers
                   else "reservation_only" if self.reservations and not self.lines
                   else "order_not_placed" if self.lines else "no_order")
        return {"outcome": outcome, "order": self.describe() if self.lines else None,
                "reservations": self.reservations, "transfers": self.transfers}


# tool definitions in plain JSON-schema form, shared by the text simulator and the voice bot
TOOLS = [
    ("lookup_item", "Get price and listed allergens of one menu item by its id.",
     {"item_id": {"type": "string"}}, ["item_id"]),
    ("add_item", "Add an item to the order. Pizzas need a size (small or large).",
     {"item_id": {"type": "string"}, "quantity": {"type": "integer"},
      "variant": {"type": "string", "description": "size, for pizzas only"},
      "notes": {"type": "string", "description": "special instructions, e.g. no onions"}},
     ["item_id", "quantity"]),
    ("set_quantity", "Change an existing order line to an exact quantity; 0 removes it.",
     {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "variant": {"type": "string"}},
     ["item_id", "quantity"]),
    ("set_fulfilment", "Set pickup or delivery, with the customer's name; delivery also needs an address.",
     {"method": {"type": "string", "enum": ["pickup", "delivery"]}, "address": {"type": "string"},
      "name": {"type": "string"}}, ["method"]),
    ("read_back", "Get the exact order text and total to read to the customer. Required before confirming.", {}, []),
    ("confirm_order", "Call ONLY after the customer clearly said yes to your latest read-back.", {}, []),
    ("place_order", "Send the confirmed order to the kitchen.", {}, []),
    ("book_table", "Book a table. Time is 24-hour HH:MM.",
     {"party_size": {"type": "integer"}, "time_hhmm": {"type": "string"}, "name": {"type": "string"}},
     ["party_size", "time_hhmm", "name"]),
    ("transfer_to_human", "Hand the call to staff. Use for allergy or health questions, complaints, "
                          "large groups, payment problems, or when the customer asks for a person.",
     {"reason": {"type": "string"}}, ["reason"]),
]


def system_prompt(menu):
    items = "\n".join(
        f"- {i['id']}: {i['name']}, " + (", ".join(f"{k} {money(v)}" for k, v in i["variants"].items())
                                          if "variants" in i else money(i["price_cents"]))
        for i in menu["items"])
    return f"""You answer the phone for {menu['restaurant']}. This is a voice call: speak in short, natural sentences, ask one question at a time, and never use lists, markdown or emoji.

Facts: open {menu['hours']['open']} to {menu['hours']['close']}; address {menu['address']}; delivery fee {money(menu['delivery_fee_cents'])}, delivery minimum {money(menu['delivery_minimum_cents'])} of food.
Menu:
{items}

Rules:
1. Never state a price, total or order content from memory. Use the tools: add_item, set_quantity, read_back.
2. Before placing an order you MUST call read_back, read exactly that text to the customer, and wait for a clear yes. Only then call confirm_order and then place_order. If anything changes after the read-back, read it back again.
3. If the customer changes their mind, fix the order with set_quantity or add_item, do not start over.
4. If something is not on the menu, say so and suggest the closest menu items. Never invent dishes.
5. Allergy, health and ingredient-safety questions: you may say which allergens the menu lists (lookup_item), but you must not promise anything is safe. Call transfer_to_human for any allergy question.
6. Complaints, requests for a person, groups larger than {menu['max_party_size']}, payment problems: call transfer_to_human.
7. You need the customer's name and pickup or delivery (with address) before the read-back.
8. Reservations: use book_table; times are 24-hour HH:MM."""
