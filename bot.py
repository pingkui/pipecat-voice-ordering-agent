"""Voice bot: browser microphone -> speech-to-text -> LLM with tools -> text-to-speech.

Run the development server (serves a prebuilt browser UI):
    python bot.py            # then open http://localhost:7860

Providers are chosen with environment variables, so each one can be swapped:
    DEEPGRAM_API_KEY                         speech-to-text
    CARTESIA_API_KEY, CARTESIA_VOICE_ID      text-to-speech
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL     any OpenAI-compatible chat API with tool calling

The ordering rules live in order_core.py (plain code, unit-tested). The model only talks and picks tools.
Per-turn latency (end of the caller's speech to the first audio of the reply) is appended to latency.jsonl.
"""
import json
import os
import time

from loguru import logger

import order_core as core
from speech_sse_tts import SpeechSSETTSService
from transcriptions_stt import TranscriptionsSTTService
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

HERE = os.path.dirname(os.path.abspath(__file__))
LATENCY_LOG = os.path.join(HERE, "latency.jsonl")
CALLS_LOG = os.path.join(HERE, "calls.jsonl")

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def build_tools(session: core.OrderSession):
    """Expose every core tool to the model; each handler just forwards to the session."""
    schemas = []
    for name, desc, props, required in core.TOOLS:
        async def handler(params: FunctionCallParams, _name=name):
            result = getattr(session, _name)(**params.arguments)
            logger.info(f"tool {_name}({params.arguments}) -> {result.get('ok')}")
            await params.result_callback(result)
        schemas.append(FunctionSchema(name=name, description=desc, properties=props,
                                      required=required, handler=handler))
    return ToolsSchema(standard_tools=schemas)


def make_stt():
    """STT_BASE_URL selects the generic `/audio/transcriptions` adapter (STT_API_KEY, STT_MODEL);
    otherwise Deepgram is used (DEEPGRAM_API_KEY)."""
    if os.environ.get("STT_BASE_URL"):
        return TranscriptionsSTTService(api_key=os.environ["STT_API_KEY"], base_url=os.environ["STT_BASE_URL"],
                                        model=os.environ["STT_MODEL"])
    return DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])


def make_tts():
    """TTS_BASE_URL selects the generic `/audio/speech` SSE adapter (TTS_API_KEY, TTS_MODEL, TTS_VOICE);
    otherwise Cartesia is used (CARTESIA_API_KEY, CARTESIA_VOICE_ID)."""
    if os.environ.get("TTS_BASE_URL"):
        return SpeechSSETTSService(api_key=os.environ["TTS_API_KEY"], base_url=os.environ["TTS_BASE_URL"],
                                   model=os.environ["TTS_MODEL"], voice=os.environ["TTS_VOICE"])
    return CartesiaTTSService(api_key=os.environ["CARTESIA_API_KEY"], voice_id=os.environ.get("CARTESIA_VOICE_ID"))


def make_llm(base_url, api_key, model, menu, extra_body=None):
    """extra_body (a dict) is merged into every chat request, e.g. {"thinking": {"type": "disabled"}} for models that
    support switching off reasoning. It can also come from the LLM_EXTRA_BODY environment variable (JSON)."""
    if extra_body is None and os.environ.get("LLM_EXTRA_BODY"):
        extra_body = json.loads(os.environ["LLM_EXTRA_BODY"])
    return OpenAILLMService(api_key=api_key, base_url=base_url,
                            settings=OpenAILLMService.Settings(model=model, system_instruction=core.system_prompt(menu),
                                                               extra=extra_body or {}))


async def run_bot(transport, session: core.OrderSession, stt, tts, llm):
    # the system prompt is set on the LLM service (see make_llm), not as a message in the context
    menu = session.menu
    context = LLMContext(messages=[], tools=build_tools(session))
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    user_agg, assistant_agg = aggregators.user(), aggregators.assistant()

    @user_agg.event_handler("on_user_turn_stopped")
    async def _customer_spoke(aggregator, strategy, message):
        session.note_customer_turn()      # lets the core know the caller answered since the last read-back

    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_measured")
    async def _log_latency(observer, secs):
        with open(LATENCY_LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "latency_secs": round(secs, 3)}) + "\n")
        logger.info(f"user-to-bot latency {secs:.2f}s")

    pipeline = Pipeline([transport.input(), stt, user_agg, llm, tts, transport.output(), assistant_agg])
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, allow_interruptions=True),
        observers=[latency],
    )

    @transport.event_handler("on_client_connected")
    async def _connected(transport, client):
        context.add_message({"role": "developer", "content": "Greet the caller briefly and ask how you can help."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(transport, client):
        with open(CALLS_LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), **session.summary()}) + "\n")
        await task.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(task)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    stt = make_stt()
    tts = make_tts()
    session = core.OrderSession(core.load_menu())
    llm = make_llm(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"], session.menu)
    await run_bot(transport, session, stt, tts, llm)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
