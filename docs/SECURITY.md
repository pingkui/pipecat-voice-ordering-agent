# Security and privacy notes

This is a demo, not a hardened service. These are the risks I know about and what is, and is not, done about them.

## Do not expose it to the internet as is
- `bot.py` serves a browser client with **no login** and calls paid APIs (speech-to-text, text-to-speech, LLM) for anyone who
  connects. Anyone who can reach the port can spend your money. It listens on `127.0.0.1`; keep it that way, or put
  authentication, rate limits and a spending cap in front of it.
- The development runner allows any web origin. Fine on localhost, not behind a public address.

## The caller is untrusted input
Whatever the caller says is transcribed and fed to the model, so the caller can try to talk the model into things
("ignore your rules", "the owner says it is free"). What limits the damage:
- **The model cannot change prices.** Totals come from the menu in code; there is no tool that sets a price.
- **The model cannot place an order without the gate** (current read-back, the caller has spoken since, no later change).
- **Tool arguments are validated** (integer quantities from 1 to 20, known item ids, opening hours, party size).
- What is **not** prevented: the model can still be talked into a wrong *phrasing*, into transferring to a person needlessly,
  or into reading a stray "yes" as a confirmation. A person should be able to review orders before the kitchen acts on them.

## Personal data
- Names, delivery addresses and what was ordered pass through the speech and model providers and appear in the bot's log
  (the INFO log prints tool arguments). `calls.jsonl` stores a summary per call with the name and address.
- Nothing here deletes or anonymises data, and there is no retention policy. Add one before using real callers.
- Recording or transcribing calls can need consent, and the rules differ by country. Check before you deploy.

## Secrets
- API keys are read from environment variables. `.env` and the log files are in `.gitignore`.
- Never commit a key; if one was ever pasted into a file in the repo, rotate it.

## If you add a phone line later
- A phone number invites **toll fraud and spam calls**. Use the carrier's spending limits and allow-lists, and cap call length.
- Phone audio is lower quality than a browser microphone; expect worse speech recognition and re-measure latency.

## Allergies
The agent lists the allergens in the menu data and routes every allergy question to a person. It is told never to promise
that a dish is safe, and the tests check that an allergy question ends in a transfer. Treat this as a safeguard, not a guarantee.
