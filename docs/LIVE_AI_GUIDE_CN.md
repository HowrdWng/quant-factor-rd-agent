# 真实 AI 使用指南（Windows）

## 推荐方式：网页内配置

1. 双击 `scripts\start_web.cmd`，或在项目目录的命令提示符中运行：

   ```bat
   scripts\start_web.cmd
   ```

2. 浏览器打开 `http://127.0.0.1:8000`。正式产品使用 FastAPI 网站，不需要 Streamlit。
3. 进入左侧 **Connections**，填写：

   - Provider：OpenAI
   - Base URL：`https://api.openai.com/v1`
   - Model：`gpt-4o-mini`
   - API key：自己的 OpenAI API Key

4. 点击 **Apply & test connection**。出现连接成功后，顶部状态应显示
   `Model · openai:gpt-4o-mini`。
5. 进入 **AI Research**，勾选 **使用真实 AI**。第一次建议继续使用
   `Synthetic · 演示` 数据，以便先验证 AI、AST 安全检查和回测全链路。
6. 提交研究问题。系统会依次执行 RAG 检索、LLM 假设、LLM 因子代码、Pydantic、AST 安全检查、
   确定性回测和有限反馈迭代。

网页中的 Key 只保存在当前 Python 服务进程内存。服务重启后需要重新填写；不会写入浏览器、
SQLite、日志或 Git 文件。

## 命令行方式

先确认当前窗口是 `cmd.exe` 还是 PowerShell，二者语法不能混用。

### Windows 命令提示符（窗口提示符通常是 `C:\...>`）

```bat
cd /d C:\path\to\quant-factor-rd-agent
set AGENT_MODE=live
set LLM_PROVIDER=openai
set LLM_API_KEY=替换成自己的真实Key
set LLM_MODEL=gpt-4o-mini
set LLM_BASE_URL=https://api.openai.com/v1
set LLM_API_STYLE=responses
quant-factor-agent test-llm
quant-factor-agent demo --mode live --auto-approve --objective "研究滞后动量因子"
```

不要在 `cmd.exe` 中使用 `$env:NAME="value"`；那是 PowerShell 语法。也不要把 Markdown 链接
`[https://...](https://...)` 填入 Base URL，只填纯 URL。

### PowerShell（窗口提示符通常以 `PS` 开头）

```powershell
Set-Location 'C:\path\to\quant-factor-rd-agent'
$env:AGENT_MODE = 'live'
$env:LLM_PROVIDER = 'openai'
$env:LLM_API_KEY = '替换成自己的真实Key'
$env:LLM_MODEL = 'gpt-4o-mini'
$env:LLM_BASE_URL = 'https://api.openai.com/v1'
$env:LLM_API_STYLE = 'responses'
quant-factor-agent test-llm
quant-factor-agent demo --mode live --auto-approve --objective '研究滞后动量因子'
```

## 如何判断 AI 真的参与了

- Connections 顶部显示 `Model · openai:gpt-4o-mini`。
- AI Research 页必须勾选 **使用真实 AI**；否则仍会明确运行 mock。
- Research Runs 的审计事件中，生成的实现 `source` 是 `llm_agent`。
- 假设和因子代码由 LLM 生成；IC、RankIC、分层收益、Sharpe、回撤等数字由 Quant Engine 计算，
  不是 LLM 编造。

## 常见错误

- `LLM_PROVIDER is mock`：仍在运行旧 Streamlit 页，或命令行环境变量没有在同一个终端窗口设置。
- HTTP 401：Key 无效、复制时带空格，或不是该 API 服务对应的 Key。
- HTTP 429：账户没有可用额度、触发速率限制或项目预算限制。
- HTTP 404 / model not found：模型名错误，或当前 API 项目无权使用该模型。
- 连接成功但研究提示未配置：网页服务被重启了；回到 Connections 重新提交 Key。
- 第三方 OpenAI-compatible 渠道：Provider 选 OpenAI-compatible，Base URL 和 Model 必须使用该渠道
  提供的值，不能混用 OpenAI 官方 Key 与第三方地址。
