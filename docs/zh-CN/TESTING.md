[English](../TESTING.md) | **简体中文**

# 测试

## 每种测试证明什么
| 命令 | 需要 | 证明 | 不证明 |
|---|---|---|---|
| `python test_core.py` | 无 | 点餐规则:尺寸、数量、总价、配送、营业时间、确认闸门 | 与模型或语音有关的任何事 |
| `python test_tools.py` | 无 | 延迟汇总的数学计算 | 延迟本身 |
| `python chat_sim.py --model M` | LLM 密钥 | 用真实模型和真实提示词,脚本化来电者最终落在正确的订单状态 | 语音识别、音质、时序 |
| `python test_pipeline.py` | LLM 密钥 | 同样的场景经过 Pipecat 的 LLM 服务、工具声明、函数调用处理和上下文聚合也能通过 | 音频通路、真实语音上的轮次检测 |
| 手动语音会话 | 全部密钥 | 整个系统端到端可用 | 通过率(次数太少) |

断言始终针对订单的**最终状态**,而不是 agent 说了什么。模型可以用一百种方式措辞;厨房会收到的是状态。

## 场景
| 场景 | 来电者做什么 | 结束时必须成立的状态 |
|---|---|---|
| `simple_pickup` | 点披萨和沙拉自取,报姓名,说好 | 已下单,总价由代码算出 |
| `change_of_mind` | 点三个披萨,然后说"改成两个" | 已下单,两个披萨,按两个计价 |
| `unknown_item` | 要菜单上没有的东西,然后点薯条 | 订单里只有薯条 |
| `special_instructions` | 汉堡不要洋葱 | 备注保存在订单行上,已下单 |
| `allergy_goes_to_staff` | 问某道菜有没有花生,说过敏很严重 | 转给店员,没有下单 |
| `no_confirmation_no_order` | 建好订单,然后说回头再打来 | 未下单 |
| `impatient_no_followup` | 让 agent 立刻下单、跳过读回 | 那一轮里没有下单 |
| `impatient_then_confirms` | 同上,读回之后说好 | 已下单 |
| `reservation_out_of_hours` | 想订凌晨 3 点的桌 | 没有预订 |
| `reservation_ok` | 四人桌,19:00 | 一条预订,四人,19:00 |

目前的结果在主 README 里。每个场景都只是单次运行、单一模型。

## 新增场景
在 `chat_sim.py` 的 `SCENARIOS` 里追加一个字典:
```python
{"name": "split_order",
 "customer": ["Two fries and a lemonade for pickup, name is Ben.", "Yes."],
 "check": lambda s, calls: (True, "") if s.placed and s.total_cents() == 1400 else (False, "wrong total")}
```
`s` 是 `OrderSession`;返回 `(ok, reason)`。`chat_sim.py` 和 `test_pipeline.py` 都会自动使用它。适合新增的场景,是来电者跟某条规则对着干的:数量 50、读回之后改地址、两个备注不同的同款菜品、来电者回答了一个没被问到的问题。

## 测量语音延迟(仍然缺失的那个数字)
语音 agent 的延迟,是来电者**说完**一句话到听到回复的**第一段音频**之间的时间。`bot.py` 恰好把这个时间按轮次记录到 `latency.jsonl`。一种站得住脚的测量方法:
1. 固定服务商和 bot 运行的区域,并把它们和结果一起写下来。
2. 用至少 30 轮的固定脚本,混合简短回答("好")、会触发工具调用的点餐请求,以及不需要工具的问题。
3. 在一天里的不同时间多跑几次。
4. 报告**中位数、p90 和 p95**,再加上低于 1 秒和低于 2 秒的轮次占比:`python tools/latency_report.py`。
5. 工具调用的轮次单独报告:它们多一次模型调用。
6. 说明哪些因素没有控制(你、服务器和服务商之间的网络距离)。

不要引用单独一轮的快速结果。在用真实密钥跑过之前,**本项目没有端到端延迟数字**;唯一测过的数字,是文本模拟器里纯模型的单次调用时间。

## 自动化测试做不到的手动检查
- **打断(barge-in):** agent 说话时你开口。它应该停下来听;记下这花了多久。
- **噪音和口音:** 试一个嘈杂的房间和一种非母语口音,记下语音识别听错了哪些词(菜品名通常最容易出错)。
- **沉默:** 读回之后保持沉默。agent 不能下单。
- **中途挂断:** 读回之后断开连接;`calls.jsonl` 里应该记录 `order_not_placed`。
