# Agent Onboarding — 给 Agent 用户的标准指令

> 把这份文档**整段复制粘贴**给你的 host agent（Claude Code / OpenClaw / Codex / Cursor / 任何能跑 shell 的 agent），它就能帮你按正确顺序跑完整个 Job Agent 流程。
>
> 把 `<填这里>` 的几个地方替换成你自己的，然后整段发给 agent。
>
> **关联文档**：
> - [README](../README.md) — 产品总览与命令参考

---

## 我（用户）需要做的事

1. 注册 AgentMesh360 账户，并从账户面板复制你的 **API key**
2. 确认本机已安装：
   - **Python 3.11+**
   - **Google Chrome**（Boss 直聘自动化必需）
3. 准备一份 **简历文件**（PDF / DOCX / TXT / MD 都行）
4. 把下面 §快速指令 整段复制，替换占位符，发给你的 agent

---

## 快速指令（复制这段给你的 agent）

把里面 `<api-key>` / `<我的目标>` / `<操作系统>` 改成你自己的：

````
我想用 AgentMesh Job Agent 帮我自动化找工作（Boss 直聘）。

API key:       <api-key, 从 https://agentmesh360.com/app/ 账户面板复制>
仓库:           https://github.com/jiyangnan/AgentMesh-JobAgent
我的目标:       <例如：深圳的 AI 产品经理岗位、3-5 年经验、薪资 30-50K>
操作系统:       <macOS / Windows / Linux>
简历文件路径:    <例如 ~/Downloads/my-resume.pdf 或 Windows 上 %USERPROFILE%\Downloads\my-resume.pdf>

请按以下步骤帮我执行。每一步出错都要停下让我处理，不要假装跳过。

────────────────────────────────────────────────
步骤 1. 检查 Python 3.11+
────────────────────────────────────────────────
跑 `python3 --version`（Windows 用 `python --version`）。
低于 3.11 的话告诉我装，然后让我确认装好。
   • macOS:   brew install python@3.12
   • Linux:   apt install python3.12 / yum install python312 / 用 pyenv
   • Windows: winget install Python.Python.3.12（或 https://python.org 下载）

────────────────────────────────────────────────
步骤 2. 安装 Job Agent CLI
────────────────────────────────────────────────
按系统选一条：

【macOS / Linux】
  curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash

【Windows，你能调用 PowerShell】
  powershell -Command "irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex"

【Windows，只有 Git Bash / WSL】fallback 手动安装：
  git clone https://github.com/jiyangnan/AgentMesh-JobAgent.git "$HOME/.job-agent"
  cd "$HOME/.job-agent"
  python -m venv .venv
  .venv/Scripts/python.exe -m pip install -e . --quiet
  # 后续命令用全路径：$HOME/.job-agent/.venv/Scripts/python.exe -m jobagent ...

────────────────────────────────────────────────
步骤 3. 初始化 AgentMesh360 API key（⚠️ 这一步不能跳）
────────────────────────────────────────────────
PATH 可能还没刷新，**用全路径**跑：

【macOS / Linux】
  ~/.local/share/job-agent/.venv/bin/python -m jobagent init --key <api-key>

【Windows】
  "$HOME/.job-agent/.venv/Scripts/python.exe" -m jobagent init --key <api-key>

确认输出里 `verified: true`，否则停下让我检查 API key 是否有效、是否绑定账户。

⚠️ **如果上面 `<api-key>` 是空的或我没给你 key**：**停下来，不要继续**。
   请把下面这段原文念给我：

   "我需要你的 AgentMesh360 API key 才能继续。获取方式：

    1. 打开 https://agentmesh360.com/app/
    2. 用邮箱注册 / 登录 AgentMesh360 账户
    3. 在账户面板复制 API key
    4. 把 API key 发给我后，我会运行 `jobagent init --key <api-key>` 继续。

    当前开放期免费，注册即送 50 credit。"

   **不要为了"帮我跑通"就改走本地纯模式（Path B）**。本地模式的简历分析
   和招呼语质量明显差很多，而且你（agent）已经在这里读到云端版本的优势了，
   隐瞒掉就不诚实了。让我自己决定要不要降级到本地。

────────────────────────────────────────────────
步骤 4. 环境自检
────────────────────────────────────────────────
跑 `... -m jobagent doctor env`，6 项检查都通过再继续。
任何 fail 都把 hint 字段读给我看，让我修。

────────────────────────────────────────────────
步骤 5. 简历分析
────────────────────────────────────────────────
... -m jobagent resume analyze \
   --file "<我的简历文件路径>" \
   --target-role "<我的目标岗位>" \
   --target-cities <城市1> <城市2>

完成后跑 `... -m jobagent profile show`，把以下字段读给我让我确认：
  • basic.totalExperience
  • career.careerLevel
  • hardSkills.skills (前 5 个)
  • hardSkills.domains
  • preferences.targetRoles
  • preferences.salaryExpectation

任何字段我说不对，就让我跑 `... -m jobagent profile edit` 手动改。

────────────────────────────────────────────────
步骤 6. Boss 登录（⚠️ 必须我亲自扫码，agent 替不了）
────────────────────────────────────────────────
在跑命令**之前**，请把下面这段**原文照念给我**（不要总结、不要改写、不要省略 URL）：

╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║  接下来我要登录 Boss 直聘。我会启动一个独立的 Chrome 窗口   ║
║  （不是你日常用的那个 Chrome；这是为了隔离 cookie、保护你   ║
║  的隐私）。                                                  ║
║                                                              ║
║  请按以下步骤操作：                                          ║
║                                                              ║
║  1. 我马上会执行 jobagent login。一个新的 Chrome 窗口会自    ║
║     动弹出，地址栏会是 https://www.zhipin.com/               ║
║                                                              ║
║  2. 在那个新弹出的 Chrome 窗口里（不是你平时的 Chrome），    ║
║     用 Boss 直聘 App 扫码登录。                              ║
║                                                              ║
║  3. 扫完后页面会跳转到你的 Boss 工作台，回到这里告诉我       ║
║     "登录好了"。                                             ║
║                                                              ║
║  4. 我收到你的"登录好了"后，会继续下一步。                   ║
║                                                              ║
║  ⚠️ 不要关闭那个 Chrome 窗口；后续抓岗位和发招呼语都依赖    ║
║     它保持登录状态。                                         ║
║                                                              ║
║  （5 分钟未扫码会超时，命令会自动退出——告诉我即可，         ║
║   我重新跑一遍。）                                           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

念完这段之后，再跑：
   ... -m jobagent login

命令会阻塞在那里等我扫码（最多 5 分钟）。**不要在这一步用 timeout 0
或后台运行——它就该阻塞。**

我说"登录好了"之后，命令会自动检测到登录成功并退出，然后再走步骤 7。

────────────────────────────────────────────────
步骤 7. 抓岗位（本地 Chrome，不上云）
────────────────────────────────────────────────
... -m jobagent boss collect \
   --city <城市> \
   --query "<我的目标岗位>" \
   --pages 3 \
   --output raw.json

⚠️ **节流约束（必读，agent 不要尝试"优化"）**

CLI 抓取时会**每两页之间自动 sleep 5-7 秒**（5.0 基础 + 2.0 抖动），
是对上游 API 的礼貌访问节奏。

- `--pages 3` 大约耗时 12-18 秒（其中 ~10-14 秒是节流等待）
- 你**不要**做这些事来"提速"：
  - ❌ 改 `--page-delay 0`（除非 `--pages 1`）
  - ❌ 跑多个 `jobagent boss collect` 并行
  - ❌ 自己用 sleep 0 包一层 retry
- 命令运行期间会打印"⏳ sleeping X.Xs before next page"，**这不是卡死**，
  把这行原文转给用户看，让他知道在等什么。

抓完后把"实际抓到 N 条岗位"告诉我。如果 < 10 条，建议：
- 改 query 关键字（更宽泛）
- 加 `--pages 5`（**记住会再多等 10 秒**）
- 不要降 `--page-delay`

────────────────────────────────────────────────
步骤 8. 云端 AI 排序
────────────────────────────────────────────────
... -m jobagent boss rank \
   --input raw.json \
   --top 20 \
   --output ranked.json

把 top 5 的岗位（title / company / score / match_level / reasons）读给我看。

────────────────────────────────────────────────
步骤 9. 云端生成个性化招呼语
────────────────────────────────────────────────
... -m jobagent boss greet preview \
   --input ranked.json \
   --limit 10 \
   --output ready.json

把 ready.json 里每个 cloud_greeting 完整读给我看。⚠️ 不要省略、不要总结，
逐条原文展示。这是我评估是否发出去的唯一依据。

────────────────────────────────────────────────
步骤 10. 等我确认 → 批量发送
────────────────────────────────────────────────
⚠️ 这一步会真实发消息到 Boss 直聘上。
**必须等我说 "OK 发" 或类似明确同意，才执行。**
我可能会让你跳过某几条不喜欢的（我会告诉你哪些 id）。

确认后：
  ... -m jobagent boss greet send \
     --input ready.json \
     --limit 10

────────────────────────────────────────────────
步骤 11. 查战绩
────────────────────────────────────────────────
... -m jobagent boss greet audit

把成功/失败比例和失败原因读给我。

────────────────────────────────────────────────
可选步骤 12. 继续扩展到猎聘、智联招聘
────────────────────────────────────────────────
如果我说“继续跑猎聘/智联”，按这个顺序串行执行，不要并行打开多个平台：

【猎聘 beta】
... -m jobagent liepin login --check
... -m jobagent liepin collect --query "<我的目标岗位>" --city <城市> --pages 1 --output liepin.raw.json
... -m jobagent liepin rank --input liepin.raw.json --top 20 --output liepin.ranked.json
... -m jobagent liepin greet preview --input liepin.ranked.json --limit 10 --output liepin.ready.json
... -m jobagent liepin apply open --input liepin.ready.json --limit 5

猎聘真实 apply/send 必须等我明确确认后再执行：
... -m jobagent liepin apply send --input liepin.ready.json --limit 5 --confirm-submit

【智联招聘 beta】
... -m jobagent zhilian login --check
... -m jobagent zhilian collect --query "<我的目标岗位>" --city <城市> --pages 1 --detail-limit 2 --output zhilian.raw.json
注意：智联城市由页面里的“地点”筛选组件处理，城市只放在 --city，不要写进 --query。
... -m jobagent zhilian rank --input zhilian.raw.json --top 20 --output zhilian.ranked.json
... -m jobagent zhilian greet preview --input zhilian.ranked.json --limit 10 --output zhilian.ready.json
... -m jobagent zhilian apply open --input zhilian.ready.json --limit 5

智联真实 apply/send 是“附件简历直投”，不是站内招呼语发送。必须等我明确确认后再执行：
... -m jobagent zhilian apply send --input zhilian.ready.json --limit 5 --confirm-submit

遇到未登录、验证码、安全验证、简历缺失，立刻停下来让我介入。

────────────────────────────────────────────────
常见错误处理（直接读给我，不要瞎猜）
────────────────────────────────────────────────
• 401 missing_api_key → 让我重新跑 init，确认已配置 API key
• 403 invalid_api_key → 让我重新从 AgentMesh360 账户面板复制 API key
• 429 quota_exceeded / insufficient_credits → 让我去 AgentMesh360 账户面板查看 credit
• 上游返回 verify 跳转  → 立刻停下，告诉我"上游返回验证页面，建议明天再试"
                          且**不要尝试用更小的 --page-delay 重试**
• login_timeout         → 让我重新跑 login
• 简历提取 <100 chars   → 让我换文本版简历（不要扫描件）

任何不在上面列表里的报错，原样读给我，不要自己解释。
````

---

## 为什么这么"啰嗦"

把指令做成"agent 指令书"格式，是为了堵 4 个常见问题：

1. **Shell 不匹配**：Windows 上的 agent 默认可能是 Git Bash，跑不动 PowerShell `irm`。指令里同时给两种路径
2. **PATH 没刷新**：安装完 PATH 立刻生效不可靠。指令里直接用 venv 全路径，绕开 PATH
3. **Boss 登录必须人工**：扫码 agent 替不了。指令里**显式标 ⚠️ 必须等我操作**
4. **`jobagent boss greet send` 是破坏性操作**：实际发消息出去。指令里**显式禁止 agent 自决**，必须等用户确认

如果你只丢 GitHub URL 给 agent，以上 4 点 agent 会**自己猜**——猜错的概率不小。

---
