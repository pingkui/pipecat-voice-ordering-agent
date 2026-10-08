#!/usr/bin/env python3
"""Deterministic tests for the ordering core. No model, no network. Run: python3 test_core.py"""
import order_core as c

M = c.load_menu()
results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name)


def new():
    return c.OrderSession(M)


def ready(s, qty=2):
    s.add_item("margherita", qty, "large")
    s.set_fulfilment("pickup", name="Sam")
    return s


def confirm(s):
    """read back, let the customer answer, confirm: the legitimate sequence"""
    s.read_back(); s.note_customer_turn(); return s.confirm_order()


s = new()
check("unknown item is rejected and lists the menu", not s.add_item("sushi", 1)["ok"] and s.lines == [])
check("pizza without a size is rejected", not s.add_item("margherita", 1)["ok"])
check("invalid size is rejected", not s.add_item("margherita", 1, "huge")["ok"])
check("quantity 0 and 21 are rejected", not s.add_item("fries", 0)["ok"] and not s.add_item("fries", 21)["ok"])
check("quantity must be an integer", not s.add_item("fries", 1.5)["ok"] and not s.add_item("fries", "2")["ok"])

s = ready(new())
s.add_item("caesar", 1)
check("totals come from the menu, not the model", s.total_cents() == 2 * 1500 + 900)
s.set_fulfilment("delivery", address="5 Test Road", name="Sam")
check("delivery fee is added by code", s.total_cents() == 2 * 1500 + 900 + 400)
check("delivery needs an address", not new().set_fulfilment("delivery")["ok"])
t = new(); t.add_item("fries", 1); t.set_fulfilment("delivery", address="x", name="A")
check("delivery below the minimum cannot be read back", not t.read_back()["ok"])

s = ready(new())
check("cannot confirm before a read-back", not s.confirm_order()["ok"])
check("cannot place before confirming", not s.place_order()["ok"])
confirm(s)
check("confirmed order can be placed", s.place_order()["ok"] and s.placed)
check("an order cannot be placed twice", not s.place_order()["ok"])

s = ready(new()); confirm(s)
s.set_quantity("margherita", 3)            # customer changes their mind after confirming
check("any change after confirmation withdraws the confirmation", s.confirmed is False)
check("a changed order cannot be placed without a new read-back", not s.place_order()["ok"])
check("a stale read-back cannot be confirmed", not s.confirm_order()["ok"])
confirm(s)
check("after a fresh read-back it can be placed", s.place_order()["ok"] and s.total_cents() == 3 * 1500)

s = ready(new()); s.read_back()
check("the model cannot confirm in the same turn as the read-back", not s.confirm_order()["ok"])
s.note_customer_turn()
check("it can confirm once the customer has answered", s.confirm_order()["ok"])
s = ready(new()); s.set_quantity("margherita", 0)
check("quantity 0 removes the line, empty order cannot be read back", s.lines == [] and not s.read_back()["ok"])
s = new(); s.add_item("burger", 1, notes="no onions"); s.add_item("burger", 1, notes="")
check("lines with different notes stay separate", len(s.lines) == 2)
s = new(); s.add_item("fries", 1); s.add_item("fries", 2)
check("identical lines merge", len(s.lines) == 1 and s.lines[0].qty == 3)

s = new()
check("table inside opening hours is booked", s.book_table(4, "19:00", "Ana")["ok"])
check("table at 03:00 is rejected", not s.book_table(4, "03:00", "Ana")["ok"])
check("table in the last hour is rejected", not s.book_table(4, "21:30", "Ana")["ok"])
check("group of 9 is rejected", not s.book_table(9, "19:00", "Ana")["ok"])
check("malformed time is rejected", not s.book_table(2, "seven", "Ana")["ok"])

s = new(); s.transfer_to_human("allergy question")
check("summary reports a transfer", s.summary()["outcome"] == "transferred_to_staff")
check("summary of an empty call", new().summary()["outcome"] == "no_order")
check("summary of a placed order", (lambda x: (confirm(x), x.place_order(), x.summary()["outcome"])[-1])(ready(new())) == "order_placed")
check("lookup lists allergens but promises nothing", "kitchen staff" in new().lookup_item("padthai")["note"])

print(f"\n{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
