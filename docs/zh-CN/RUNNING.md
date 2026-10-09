[English](../RUNNING.md) | **简体中文**

# 运行

## 1. 安装
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # 固定了 pipecat-ai 1.12.0 以及本项目用到的扩展
```
需要 Python 3.11 或更新版本(pipecat-ai 1.12.0 的要求)。首次启动不会下载大文件;Silero 语音活动检测模型随包提供。

## 2. 运行不需要密钥的测试
```bash
.venv/bin/python test_core.py      # 31 项点餐规则检查
.venv/bin/python test_tools.py     # 7 项辅助工具检查
```

## 3. 用模型运行场景测试(纯文本)
需要任意支持工具调用的 OpenAI 兼容聊天接口。
```bash
export LLM_BASE_URL=https://.../v1
export LLM_API_KEY=...
export LLM_MODEL=...
.venv/bin/python chat_sim.py --model $LLM_MODEL            # 10 个脚本化来电者,断言最终订单状态
.venv/bin/python test_pipeline.py                          # 同样的场景经过真实的 Pipecat 流水线
```
每个场景会调用模型多次,所以会在你的服务商那里花一点钱。`chat_sim.py --only NAME --repeat 3` 可以把一个场景重复跑几次,是判断场景是否不稳定的最快办法。

## 4. 运行语音 bot
需要的账号(各服务商有各自的价格和免费额度,开始前请自行确认):
- **语音识别:** Deepgram,`DEEPGRAM_API_KEY`。
- **语音合成:** Cartesia,`CARTESIA_API_KEY` 和 `CARTESIA_VOICE_ID`(从他们的声音库里选一个)。
- **LLM:** 与上面相同的三个变量。要有接近电话的手感,请用**快速的非推理模型**;推理模型在第一个 token 之前要花好几秒(这里实测:推理模型每次调用中位数 2.4 秒、p90 4.4 秒,纯文本)。

如果你的模型支持关闭推理,请关掉:把 `LLM_EXTRA_BODY` 设成一个 JSON 对象,它会被并入每个聊天请求,例如对 Kimi 用 `export LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}'`(粗略测量:首个 token 约 1.4 秒,而不是约 4.8 秒)。`bot.py` 和 `chat_sim.py` 都会读取它。

```bash
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=... CARTESIA_VOICE_ID=...
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
.venv/bin/python bot.py
```
打开 **http://localhost:7860/client/**,允许使用麦克风,点连接,然后说话。服务只监听 `127.0.0.1`。

更换服务商只需要改 `bot.py`:把 `DeepgramSTTService` 或 `CartesiaTTSService` 换成别的 Pipecat 服务类(例如其他语音识别或语音合成厂商),流水线其余部分保持不变。

### 从另一台机器使用
浏览器只在 `localhost` 或 HTTPS 下允许使用麦克风。访问服务器上运行的 bot,最简单又安全的办法是 SSH 隧道,然后在你自己的电脑上打开 `http://localhost:7860/client/`:
```bash
ssh -L 7860:127.0.0.1:7860 user@your-server
```
没有认证和限流时,不要把 bot 绑定到公网地址;见 `SECURITY.md`。

## 5. 运行会写出什么
| 文件 | 内容 |
|---|---|
| `latency.jsonl` | 每轮一行:从来电者说完到第一段回复音频的时间 |
| `calls.jsonl` | 每通结束的电话一行摘要(结果、订单、预订、转接) |
| `runs/*.json` | `chat_sim.py` 的对话记录和工具调用 |

三个文件都被 git 忽略。用 `python tools/latency_report.py` 汇总延迟。用 `python tools/make_sample_calls.py > docs/examples/sample_calls.md` 把 `runs/` 渲染成可读的示例通话。

## 故障排查
| 现象 | 可能原因 |
|---|---|
| 客户端连接时报 `KeyError: 'DEEPGRAM_API_KEY'`(或别的密钥) | 运行 `bot.py` 的 shell 里没有导出这个变量 |
| 页面能打开,但没有音频发出 | 麦克风权限被拒绝,或页面不在 `localhost`/HTTPS 下 |
| bot 有回答,但要等好几秒 | LLM 太慢(推理模型、距离远的区域);看 `latency.jsonl` 和日志里的 `user-to-bot latency` |
| bot 反复问姓名或自取/配送 | 设计如此:两者都设置之前 `read_back` 会被拒绝 |
| 某个场景失败一次、下次又通过 | 模型的随机性;用 `--repeat 3` 重跑,并查看 `runs/` 里的对话记录 |
| `ModuleNotFoundError: fastapi` | 缺少 `runner` 扩展;请按 `requirements.txt` 重新安装 |
