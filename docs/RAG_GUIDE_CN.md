# 本地研报 RAG 与真实 AI 使用指南

## 1. 数据放置

默认扫描 `QUANT_DATA_ROOT`，未配置时使用项目的 `workspace` 目录。完整的 PDF、Markdown、TXT 会进入 Research Knowledge Base；`.part`、CSV、
Excel、Parquet 不会进入 RAG。

你自己的研究笔记可以放到任意子目录，例如：

```text
D:\quant-data\my_notes\volume_momentum.md
```

## 2. 建立或更新索引

```bat
cd C:\path\to\quant-factor-rd-agent
python -m quant_factor_agent.cli index-rag --root D:\quant-data --workers 4
```

索引可断点续跑：未变化文件直接跳过，新下载完成的 PDF 才会加入。页面中的
“Knowledge & Data → 增量建立 RAG 索引”执行同一个过程。

下载任务长时间运行时，可以另开一个终端持续接收新完成的 PDF：

```bat
python -m quant_factor_agent.cli watch-rag --root D:\quant-data --workers 4 --interval 60
```

状态解释：

- `indexed`：已抽取文字，可被检索并带页码引用。
- `needs_ocr`：扫描图片型 PDF，没有足够可检索文本；后续需要 OCR。
- `failed`：PDF 本身损坏或页树无法解析，需要重新获得源文件。

## 3. 启动网站

```bat
python -m quant_factor_agent.web --host 127.0.0.1 --port 8000
```

也可以双击 `scripts\start_web.cmd`，然后打开 `http://127.0.0.1:8000`。

## 4. 连接真实模型

进入 Connections：

1. Provider 选择 OpenAI 或 OpenAI-compatible。
2. Base URL 填纯 URL，例如 `https://api.openai.com/v1`，不要粘贴 Markdown 链接。
3. 填模型名称和 API key。
4. 点击 Apply & test connection，看到连接成功后回到 AI Research。

Key 只保存在当前服务进程内存，不写入浏览器存储、SQLite 或代码库。服务重启后需要重新连接；
部署环境也可以用 `.env.example` 中的环境变量。

## 5. 一键研究怎样使用 AI

```text
自然语言问题
  → 本地 FTS5/BM25 检索与轻量 rerank
  → RD Agent 基于 evidence IDs 生成可证伪 hypothesis
  → DataCatalogTool 判断真实字段和本地导出状态
  → Development Agent 生成一个受限 compute_factor 函数
  → Pydantic + AST policy + smoke test
  → MarketDataProvider 提供结构化 DataFrame
  → Quant Engine 计算 IC / RankIC / 五分位 / Sharpe / Drawdown
  → Development / Validation / Walk-forward
  → 最多 0–3 次反馈修改
  → 带文件、页码和 chunk 的研究结论
```

AI 负责：检索理解、假设、因子定义、受限代码和结果解释。

AI 不负责：伪造行情、自己填写回测数字、绕过 AST、访问 Broker、直接下单。

## 6. 切换真实行情

当前页面先用 Synthetic 演示。等数据根目录的 `csmar_exports` 出现实表后，在 Research 的“数据”
选择 `Local CSMAR · 真实`。首选支持：

- `trd_fwardquotation.csv`
- `trd_bwardquotation.csv`
- `trd_dalyr.csv`

大型 CSV 请求必须给时间范围或股票池，以免一次把数百万行全部透视到内存。A 股 CSMAR 不能用于
美股问题；其他市场以后通过新的 `MarketDataProvider` 接入，而不是修改 Quant Engine。

## 7. 从研究到交易

V1 正确路径是：

```text
Historical Data → Factor Research → Backtest → Walk-forward/OOS
→ Validated Signal → RiskRules → PaperBrokerAdapter
```

Paper Trading 是本地模拟账户。真实 Broker 只保留 `BrokerAdapter` 插件边界，LLM 从来拿不到
`place_order()` 对象。
