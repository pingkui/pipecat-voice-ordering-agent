[English](README.md) | **简体中文**

# 语音点餐 Agent(Pipecat):模型负责说话,代码负责决定

一个餐厅电话风格的语音 agent:接受点餐、回答菜单问题、预订座位,并在该转人工时转给真人。来电者在浏览器标签页里通话(不需要电话号码)。

核心设计原则:**模型说话,代码决定。** 菜单查询、价格和总价、数量、营业时间,以及"只有来电者对订单的*当前*内容确认过读回之后才能下单"这条规则,全部写在 `order_core.py` 里,是带自测的普通代码。模型只负责选择调用哪个工具、以及组织回复的措辞。

```
浏览器麦克风 -> 语音识别 -> LLM(工具调用) -> 语音合成 -> 浏览器扬声器
                              |
                        order_core.py   (菜单、价格、营业时间、确认规则)
```

## 文档
| 文档 | 内容 |
|---|---|
| [`docs/zh-CN/ARCHITECTURE.md`](docs/zh-CN/ARCHITECTURE.md) | 组件、工具、以状态机表示的确认闸门、设计取舍 |
| [`docs/zh-CN/RUNNING.md`](docs/zh-CN/RUNNING.md) | 安装、密钥、运行测试和语音 bot、故障排查 |
| [`docs/zh-CN/TESTING.md`](docs/zh-CN/TESTING.md) | 每种测试证明什么、场景表、如何新增场景、如何正确测量语音延迟 |
| [`docs/zh-CN/SECURITY.md`](docs/zh-CN/SECURITY.md) | 暴露风险、不可信的来电者、个人数据、密钥、电话线路风险 |
| [`docs/examples/sample_calls.md`](docs/examples/sample_calls.md) | 真实的脚本通话,含工具调用和最终状态(英文,因为 agent 说的是英文) |

## 仓库内容
| 文件 | 作用 |
|---|---|
| `order_core.py`、`menu.json` | 点餐逻辑和一份虚构菜单 |
| `bot.py` | Pipecat 语音 bot(浏览器 WebRTC 传输、Silero VAD、Deepgram 语音识别、Cartesia 语音合成、任意 OpenAI 兼容的 LLM) |
| `test_core.py` | 31 项确定性检查,不用模型、不联网 |
| `chat_sim.py` | 脚本化的来电者通过相同的提示词和工具与真实模型对话,断言最终订单状态 |
| `test_pipeline.py` | 同样的场景经过真实的 Pipecat 流水线(LLM 服务、工具声明、处理函数、上下文聚合),语音部分换成文本 |
| `speech_sse_tts.py` | 语音合成适配器,适用于以服务器推送事件流式返回的 `/audio/speech` 接口(针对 `qwen3-tts-flash` 编写) |
| `test_tts_adapter.py` | 针对该适配器、在本地假服务器上的 13 项离线检查 |
| `test_tools.py` | 7 项检查,验证延迟汇总的计算 |
| `tools/` | `latency_report.py` 汇总 `latency.jsonl`;`make_sample_calls.py` 把场景运行结果渲染成可读的通话 |

## 不依赖模型表现的保证
- 价格和总价由代码根据菜单计算,模型从不凭记忆报价。
- 披萨必须选尺寸;菜单外的菜品、不合法的数量(不是 1 到 20、不是整数)、营业时间外或人数超限的预订,都会被拒绝,并附上模型可以转述的原因。
- **确认闸门:** 除非 (a) 订单最后一次改动之后调用过 `read_back`,**并且** (b) 来电者在该次读回之后说过话,否则 `confirm_order` 会失败。之后任何改动都会撤销确认。因此订单不可能在读回的同一轮里被下单,过期的读回也无法被确认。
- 过敏和健康类问题转给真人;菜单数据只列出过敏原,并注明须由店员确认。

## 目前的结果
- `test_core.py`:**31/31** 项检查通过。
- `chat_sim.py`,真实模型(Kimi `kimi-k2.6`),**10/10** 个场景通过:简单取餐、点餐途中改主意、菜单外的菜品、特殊备注、过敏问题转店员、没有明确"好"就不下单、着急的来电者(两个)、营业时间内外的订座。
- `test_pipeline.py`,同一模型经过 Pipecat 1.12.0:**10/10** 个场景通过。
- 开发服务器可启动,浏览器客户端在 `127.0.0.1:7860`。
- 文本模拟中测得的纯模型延迟:**每次 LLM 调用中位数 2.4 秒,p90 4.4 秒**(46 次调用)。这是推理型模型的原始调用时间,没有流式送入语音;对自然的电话对话来说太慢,所以 `bot.py` 的 LLM 通过环境变量配置,可以换成快速的非推理模型。

- 关掉推理模式效果明显(从这台服务器到 Kimi API 实测,流式,每种设置 3 次,所以只是粗略数字):`kimi-k2.6` 配合 `{"thinking": {"type": "disabled"}}` 时,首个 token 的中位数是 **1.36 秒**,默认设置是 4.84 秒(三次里只有一次成功)。关掉推理后重跑了场景:**10/10 通过**,整次调用中位数 1.9 秒,p90 2.5 秒(55 次调用)。通过 `LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}'` 设置。第一次关掉推理跑时有一个场景失败,但那是脚本的问题,不是 agent 的问题:模型多问了一句"自取吗?",占用了来电者的确认那一句(见 `docs/zh-CN/TESTING.md` 里的 `finish_with_yes`)。

- 通过适配器实测语音合成(网关后面的 `qwen3-tts-flash`,6 次请求,音色 `Cherry`):**首段音频的中位数是 1.10 秒**(最大 1.33 秒),合成每一秒音频大约需要 0.36 秒,跑在播放前面。输出是有效的 24 kHz、16 位单声道音频。首段音频的时间是叠加在模型首个 token 之后的,所以要计入通话的延迟预算。

## 尚未完成(在完成之前请保持怀疑)
- **还没有端到端的语音运行。** 语音识别和语音合成需要服务商密钥,构建时没有,所以真实音频延迟、打断(barge-in)和语音识别准确率都**没有测量**。`bot.py` 会把每一轮的"来电者说完到收到回复"的延迟记录到 `latency.jsonl`;`docs/zh-CN/TESTING.md` 说明如何把它变成站得住脚的数字(`tools/latency_report.py`)。目前还没有这个数字。
- 场景运行是单次、单一模型:只说明测试框架和规则可用,不是通过率。
- 没有电话线路(Twilio/SIP),也没有对接 POS;订单保存在内存里,以摘要形式写入 `calls.jsonl`。
- 脚本化来电者礼貌且表达清楚。嘈杂的音频、口音、抢话的人都没有覆盖。

## 运行
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_core.py                       # 不需要密钥
.venv/bin/python test_tools.py
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
.venv/bin/python chat_sim.py --model $LLM_MODEL     # 文本模式场景
.venv/bin/python test_pipeline.py                   # 同样的场景经过 Pipecat
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=... CARTESIA_VOICE_ID=...
.venv/bin/python bot.py                             # 然后打开 http://localhost:7860/client/
```
`bot.py` 只监听本机。它会调用收费的 API,所以没有认证和限流的情况下不要暴露到互联网。

## 许可证
MIT,见 `LICENSE`。
