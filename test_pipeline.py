#!/usr/bin/env python3
"""Runs the scripted scenarios through a real Pipecat pipeline (LLM service, tool schema, function-call handlers,
context aggregation) with the speech parts replaced by text. It proves the voice bot's brain is wired correctly
without needing speech-to-text or text-to-speech keys.

  LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=... python test_pipeline.py [--only NAME]
"""
import argparse
import asyncio
import os
import sys
import time

from loguru import logger

import bot as voice_bot
import chat_sim
import order_core as core
from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner


class Tap(FrameProcessor):
    """Stands in for text-to-speech: records what the bot would have said and when it is idle."""

    def __init__(self):
        super().__init__()
        self.text, self.pending_calls, self.responses_done, self.last_event = [], 0, 0, time.time()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.text.append(frame.text)
        elif isinstance(frame, FunctionCallInProgressFrame):
            self.pending_calls += 1
        elif isinstance(frame, FunctionCallResultFrame):
            self.pending_calls -= 1
        elif isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)):
            if isinstance(frame, LLMFullResponseEndFrame):
                self.responses_done += 1
        self.last_event = time.time()
        await self.push_frame(frame, direction)


def settled(context):
    """True when the conversation ends on a spoken assistant message, not on a tool call or a tool result
    (after a tool call the model is called again, which can take seconds without any frame flowing)."""
    msgs = context.get_messages()
    last = msgs[-1] if msgs else {}
    return last.get("role") == "assistant" and not last.get("tool_calls") and bool(last.get("content"))


async def wait_idle(tap, context, started_responses, timeout=180):
    """Idle = a response finished after we asked, no tool call pending, the context ends on spoken text."""
    end = time.time() + timeout
    while time.time() < end:
        if (tap.responses_done > started_responses and tap.pending_calls == 0
                and settled(context) and time.time() - tap.last_event > 0.5):
            return
        await asyncio.sleep(0.2)
    raise TimeoutError("pipeline did not become idle")


async def run_scenario(sc, base_url, key, model):
    session = core.OrderSession(core.load_menu())
    llm = voice_bot.make_llm(base_url, key, model, session.menu)
    context = LLMContext(messages=[], tools=voice_bot.build_tools(session))
    assistant_agg = LLMContextAggregatorPair(context).assistant()
    tap = Tap()
    task = PipelineWorker(Pipeline([llm, tap, assistant_agg]), params=PipelineParams(allow_interruptions=False))
    wr = WorkerRunner(handle_sigint=False)
    await wr.add_workers(task)
    runner = asyncio.create_task(wr.run())
    await asyncio.sleep(0.5)
    t0 = time.time()
    for line in sc["customer"]:
        before = tap.responses_done
        context.add_message({"role": "user", "content": line})
        session.note_customer_turn()
        await task.queue_frames([LLMRunFrame()])
        await wait_idle(tap, context, before)
    await task.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    ok, why = sc["check"](session, [])
    return ok, why, time.time() - t0, "".join(tap.text)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    base_url, key, model = os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"]
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    passed = total = 0
    for sc in chat_sim.SCENARIOS:
        if a.only and sc["name"] != a.only:
            continue
        total += 1
        try:
            ok, why, secs, spoken = await run_scenario(sc, base_url, key, model)
        except Exception as e:                      # a crashed scenario is a failed scenario
            ok, why, secs, spoken = False, f"{type(e).__name__}: {e}", 0, ""
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {sc['name']}  {secs:.0f}s" + ("" if ok else f"  [{why}]"))
    print(f"\n{passed}/{total} scenarios passed through the Pipecat pipeline")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
