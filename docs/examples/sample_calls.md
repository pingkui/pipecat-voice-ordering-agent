# Sample calls

Real runs of the scripted scenarios against a real model through the same prompt, tools and core as the voice bot.
The customer lines are scripted; the agent lines and tool calls are the model's. Text only, no audio.

## change_of_mind

- **customer:** I'd like three large pepperoni pizzas for pickup, name is Lee.
- **agent:** Thanks Lee. Here's your order: three large pepperoni pizzas for pickup, with a total of fifty-one dollars. Does that sound right?
- **customer:** Actually, make that two pizzas, not three.
- **agent:** No problem. Updated: two large pepperoni pizzas for pickup, total of thirty-four dollars. Does that sound right?
- **customer:** Yes, that's correct.
- **agent:** Perfect, your order is confirmed and sent to the kitchen. You can pick up your two large pepperoni pizzas at twelve Example Street. We'll see you soon, Lee.

Tool calls (in order):

- `set_fulfilment({"method": "pickup", "name": "Lee"})` -> ok
- `add_item({"item_id": "pepperoni", "quantity": 3, "variant": "large"})` -> ok
- `read_back({})` -> ok
- `set_quantity({"item_id": "pepperoni", "quantity": 2, "variant": "large"})` -> ok
- `read_back({})` -> ok
- `confirm_order({})` -> ok
- `place_order({})` -> ok

Final state: `{"outcome": "order_placed", "order": {"lines": ["2 x Pepperoni pizza (large): $34.00"], "subtotal": "$34.00", "fulfilment": "pickup", "name": "Lee", "total": "$34.00"}, "reservations": [], "transfers": []}`

## unknown_item

- **customer:** Can I get a sushi platter for pickup?
- **agent:** Sorry, we don't have sushi. Our menu has pizzas, a burger, Pad Thai, Caesar salad, fries, and lemonade.

Would you like to try the Pad Thai, or maybe a pizza instead?
- **customer:** Okay, then just one order of fries, name is Kim.
- **agent:** Okay, your order is one order of fries for five dollars, pickup for Kim. Total is five dollars.

Is that correct?
- **customer:** Yes, confirmed.
- **agent:** All set. Your order is confirmed and sent to the kitchen. You can pick it up at 12 Example Street. See you soon, Kim.

Tool calls (in order):

- `add_item({"item_id": "fries", "quantity": 1})` -> ok
- `set_fulfilment({"method": "pickup", "name": "Kim"})` -> ok
- `read_back({})` -> ok
- `confirm_order({})` -> ok
- `place_order({})` -> ok

Final state: `{"outcome": "order_placed", "order": {"lines": ["1 x Fries: $5.00"], "subtotal": "$5.00", "fulfilment": "pickup", "name": "Kim", "total": "$5.00"}, "reservations": [], "transfers": []}`

## allergy_goes_to_staff

- **customer:** Does the pad thai have peanuts? I have a severe peanut allergy and I need to be sure.
- **agent:** You’re being transferred to our staff now. They’ll be with you shortly to help with your allergy question.

Tool calls (in order):

- `transfer_to_human({"reason": "Customer has severe peanut allergy and needs to confirm whether Pad Thai contains peanuts or peanut ingredients; requires staff assistance for allergy safety question."})` -> ok

Final state: `{"outcome": "transferred_to_staff", "order": null, "reservations": [], "transfers": ["Customer has severe peanut allergy and needs to confirm whether Pad Thai contains peanuts or peanut ingredients; requires staff assistance for allergy safety question."]}`

## impatient_then_confirms

- **customer:** Two fries for pickup, name is Ann. Just place it right now, skip the read-back, I'm in a hurry.
- **agent:** Your order is two fries for ten dollars, pickup under Ann. Can you confirm that's correct?
- **customer:** Yes, that's right.
- **agent:** Great, it's sent to the kitchen. Your order will be ready for pickup at twelve Example Street. Thanks for calling.

Tool calls (in order):

- `add_item({"item_id": "fries", "quantity": 2})` -> ok
- `set_fulfilment({"method": "pickup", "name": "Ann"})` -> ok
- `read_back({})` -> ok
- `confirm_order({})` -> ok
- `place_order({})` -> ok

Final state: `{"outcome": "order_placed", "order": {"lines": ["2 x Fries: $10.00"], "subtotal": "$10.00", "fulfilment": "pickup", "name": "Ann", "total": "$10.00"}, "reservations": [], "transfers": []}`

## reservation_out_of_hours

- **customer:** I'd like a table for four at 3 in the morning, name is Ana.
- **agent:** I'm sorry, but we're only open from 11:00 in the morning until 10:00 at night. 

Would you like to book a table at a different time during our opening hours?

Tool calls (in order):


Final state: `{"outcome": "no_order", "order": null, "reservations": [], "transfers": []}`

