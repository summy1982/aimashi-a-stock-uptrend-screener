# Aimashi A股主板主升浪筛选系统

Aimashi 是一个面向 A 股主板的“新闻热点 + 技术分析 + 大模型研判”桌面工具。程序启动后可联网抓取多源财经新闻，使用兼容 OpenAI 的大语言模型分析热点、映射主板板块，再结合实时行情、技术指标和价格行为学筛选未来 3 天值得观察的主升浪候选股。

> 本项目开源给所有人学习、二次开发和改进。输出结果仅用于研究辅助，不构成任何投资建议；市场有风险，交易需自行决策并控制仓位。

## 核心功能

- 多源新闻抓取：覆盖国内财经媒体、海外财经媒体、央行/监管机构、能源、有色、AI、宏观市场等信息源。
- 新闻深度分析：不仅分析标题，也会尽量读取正文内容，由 LLM 总结热点、识别预期差、判断产业链传导路径。
- 板块映射：将新闻热点映射到 A 股主板相关板块，过滤非主板标的。
- 候选池筛选：每个热点板块默认输出 2-5 只候选股，避免最终结果过度集中在单一股票。
- 技术分析：结合 MACD、RSI、KDJ、BOLL、均线、ATR、量价结构、支撑位、压力位、风险收益比进行评分。
- 稳定兜底：模型超时或返回异常时，程序会使用本地规则继续生成候选池，不会中断整个流程。
- 对话复盘：分析完成后可围绕输出股票继续向 AI 提问，查看逻辑、风险、价位和观察条件。
- Windows EXE：普通用户下载 Release 中的 EXE 后，填写 Base URL / API Key / Model 即可使用。

## 下载使用

1. 打开 GitHub Releases，下载最新版本的 `AimashiAStock.exe`。
2. 双击启动软件。
3. 在界面设置中填写：
   - `Base URL`：推荐 `https://xinyuanai666.com/v1`
   - `API Key`：填写你的 OpenAI 兼容接口密钥
   - `Model`：填写可用模型名称，例如 `gpt-4o`、`gpt-4.1` 或你的中转站支持的模型名
4. 点击“开始分析”，等待新闻抓取、热点分析、板块映射和技术研判完成。
5. 在仪表盘查看候选股，也可以在对话面板继续追问单只股票的逻辑和风险。

## 从源码运行

要求：Windows 10/11，Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

首次运行会在项目目录生成 `config.json`。你也可以参考 `config.example.json` 手动创建配置。

## 配置说明

| 配置项 | 说明 |
| --- | --- |
| `openai_base_url` | OpenAI 兼容接口地址，推荐 `https://xinyuanai666.com/v1` |
| `openai_api_key` | API 密钥，请不要提交到 Git 仓库 |
| `model` | 模型名称，需与你的接口服务保持一致 |
| `top_sectors` | 新闻分析后保留的热点板块数量 |
| `top_stocks` | 总候选股数量上限 |
| `min_per_sector` | 每个板块最少候选数，默认 2 |
| `max_per_sector` | 每个板块最多候选数，默认 5 |
| `news_per_source` | 每个新闻源抓取上限，默认 40 |
| `news_total_limit` | 全部新闻总上限，默认 3000 |
| `news_workers` | 新闻抓取并发数 |
| `stock_workers` | 个股技术分析并发数 |

## 打包 EXE

```powershell
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --windowed --name AimashiAStock main.py
```

打包完成后，可执行文件位于 `dist\AimashiAStock.exe`。

## 常见问题

**新闻源数量为什么会变化？**

新闻网站、RSS、海外媒体经常存在访问限制、反爬、地区网络差异或当天更新不足。软件会尽量抓取可用来源，并在日志中展示每个来源的实际数量。

**阶段 2 分析较慢怎么办？**

新闻全文分析会消耗较多 token，模型服务响应速度会直接影响耗时。软件会自动启用规则兜底继续运行；也可以换更快的模型，或适当降低 `news_per_source`、`news_total_limit`。

**为什么没有候选股？**

当新闻和技术面共振不足、实时行情无法获取或市场整体风险较高时，软件可能降低置信度。新版会尽量保留候选池并给出观察条件，而不是强行给出确定性买入结论。

**结果可以直接买入吗？**

不可以。软件提供的是研究线索和观察名单，不是买卖指令。任何交易都需要结合实时盘口、仓位管理、止损纪律和个人风险承受能力。

## 开源许可

本项目使用 MIT License。欢迎 fork、提交 issue、改进新闻源、优化提示词、扩展数据源和技术指标。
