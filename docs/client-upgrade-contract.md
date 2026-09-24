# Job Agent 客户端升级契约

本文定义公开 CLI 从历史版本原地升级时，哪些本地资产必须保留、哪些状态可以自动清理、哪些数据需要迁移，以及什么情况必须阻断平台操作。它是客户端状态兼容性的唯一工程依据。

关联文档：[用户指南](../README.md)、[Agent 工作流](./agent-onboarding.md)、[Codex 原生操作 Skill](../skills/codex-job-agent/SKILL.md)。

## 工作流协议的兼容性

工作流协议 V2 新增 `action` 和 `workflow contract/submit/next/advance/status`，
保留 V1 `agent_action` 与 `workflow-contract`。旧签名协议和 BrowserWork 许可不改变。
新 `state/workflow.json` 记录账户、需求、动作版本、执行意图和结果；与
`round_criteria_input.json` 一同加入账户切换归档。动作在 dispatch 前持久化领取；
丢失结果不重发原动作，已有 nonce 不作为新许可返回。只读查询不创建求职轮次。

当前 round 可增加 `round_criteria`、`criteria_history`、`direction_change`、
`delivery_followup`、`cancelled_delivery_lists`。缺少字段的旧 round 按原合同读取。
条件版本绑定新的完整预览；旧清单和历史回执保留，但不能继续使用旧授权。
新增取消记录只影响取消当时的清单，旧版已经跳过的平台不会复活。
同步记录新增 `policy_version=2`、平台账号与 `basis=user_attested`。
旧记录缺少 policy_version 时按 V1 的可恢复 hold 处理；不自动转换成跳过。
不清除凭据、浏览器 profile、候选、审计或未知投递结果。

Server 的新表均以 CREATE TABLE IF NOT EXISTS 增量初始化；既有轮次、绑定与
收费表不重写。新接口未部署时明确报错并保留用户输入，不回退绕过新分支。
此扩展不授权旧客户端继续处理已经使用 V2 条件或取消分支的轮次；应使用当前版本。

`agent_action` 为新增输出字段；现有 `next_suggested`、交互协议、签名、
BrowserWork nonce/binding 及投递授权保持原有校验。
`jobagent workflow-contract` 不联网、不迁移、不读取账户状态。

`pending_round_binding.json` 可新增可选 `round_request`，保存当前用户明确提供的
岗位、城市和本地档案选择。文件继续受既有账户隔离与归档规则保护；旧文件缺少
该字段时按原样读取，不补造用户意图。新轮次成功创建后清除暂存请求；换简历时
保留本轮已给出的输入。未发生配置清空或简历重写，不需要提高状态 schema。
旧客户端不认识的新字段不可作为业务事实另行推断；继续已有交互时应使用当前版本。

## 目标

已安装旧版的客户升级后，应当直接得到一个可继续工作的客户端，而不是由宿主 Agent 猜测是否需要清缓存、重登平台或删除目录。升级过程必须满足：

1. 不丢 API Key、招聘网站登录态、简历画像、审计记录和用户偏好。
2. 自动清理只对可重建的临时状态生效。
3. 旧 schema 有明确、幂等的数据迁移。
4. 无法安全修复的冲突阻断真实平台动作，并返回机器可读恢复命令。
5. 同一迁移重复运行不会再次清理、归档或改写用户数据。

## 安装后的设置交接

官方安装器每次成功安装、重装或更新后，都输出 `onboarding_handoff`。
`jobagent onboarding` 仅只读检查本机凭据是否可读取，不联网、不获取业务锁、
不触发自动升级、迁移、Skill 写入、浏览器工作或计费，也不写入 onboarding 完成标记。
没有 Key 时返回申请地址、回到原 Agent 对话的说明和初始化入口；已有 Key 时返回
`jobagent doctor env`，不把凭据存在视为在线验证成功。不可读的配置保持原样并明确交接。
`init` 成功后统一先执行环境检查；账户归属错误仍保留原恢复命令。
所有 API Key、账户归属、画像、轮次、原生任务、授权及审计继续遵循下表，无新增迁移。

## 状态处理矩阵

| 本地资产 | 升级动作 | 约束 |
|---|---|---|
| `credentials` | 保留并校验 | 旧 Key 格式不删除；阻断平台命令并要求用 `jobagent init` 替换 |
| `state_owner.json` | 新增并校验 | 新空状态可自动绑定；历史业务状态必须由用户执行 `jobagent account bind --confirm-legacy` 显式认领；账户不一致时 fail closed |
| `accounts/<account_ref>/state/` | 保留 | 切换账户时保存非活动账户的画像、轮次、Discover、升级归档与审计；返回原账户时恢复，不删除 |
| Job Agent Chrome profile / cookies | 永远保留 | 自动升级不得删除或重建浏览器 profile；需要重新登录时由平台登录检查显式提示 |
| `state/profile.json` | 保留并校验 schema | 可兼容则原样保留；不兼容时阻断平台命令并要求重新分析简历 |
| 四个平台 audit log | 永远保留 | 它们是投递、消息送达和去重证据，不参与缓存清理 |
| `state/browser-work.sqlite3` 与原生会话绑定 | 保留并只读预检 | 不清理、不重建、不迁移此账本；存在未决副作用或无法安全读取时暂停升级，不能将缺失回执当作未发送 |
| `accounts/<account_ref>/state/analytics_spool.json` | 按账户保留 | 仅保存最多 25 条去标识化 committed facts 与固定去重标记，文件权限 `0600`；固定保存在既有账户命名空间中，降级期间切换账户也不会移动或误认，owner 或 Key 证明不一致时不得发送 |
| `state/support_state.json` | 保留 | 首次投递后的单次提示状态不得因升级重置 |
| `state/current_round.json` | 按 schema 迁移 | v2 活动 round 原样保留平台进度并标记 `legacy_implicit` 目标岗位意图；状态迁移 v4 将旧版尚未发送的 `reviewed` 平台退回 `awaiting_delivery_confirmation`，不得沿用旧自动发送命令；状态迁移 v7 仅允许尚未产生候选、签名决策、预览、授权或投递证据的活动轮次重新绑定用户明确更新后的画像；更旧且含义不明确的平台状态重置为安全的待执行状态；损坏 JSON 保留到 archive 后重建 |
| `state/rounds/` | 保留 | 历史轮次不覆盖、不删除 |
| `state/discoveries/` | 同协议保留，协议变化时归档 | 归档到 `state/archive/`，不得连同 audit 一起删除；状态迁移 v4 只移除旧 review 文件中的自动继续预览与旧授权，保留签名 manifest、`send_candidates`、用户提升项和 pending Discover，从 review 原地重建清单，不重新采集或收费 |
| `state/pending_interaction.json` | 按交互类型迁移 | 目标岗位等仍有效交互原样保留；旧投递确认交互在迁移 v4 清除并从保留的 review 重新生成，避免旧交互 ID 授权新清单 |
| release manifest cache | 自动清理 | 新版本重新获取并验证签名策略 |
| platform tab / browser-session marker | 仅旧迁移按需清理 | v7 → v8 同协议迁移原样保留；旧迁移仅清理可重建的 CDP 映射，明确标记的原生会话始终保留，不触碰 Chrome cookies/profile |
| last doctor / probe 输出 | 自动清理 | 旧诊断结论不应冒充新版状态 |
| activity / browser / update lock | 死亡进程自动清理 | 锁所属 PID 存活时阻断迁移；不得抢占真实运行中的命令 |
| logs | 保留 | 用于跨版本排障；不得写入 API Key 等秘密 |
| 用户配置 | 校验，不覆盖 | 新版默认值不能静默覆盖用户配置；不兼容项必须显式报告 |

Analytics relay 使用已配置 API Key 在后台向 `/v1/analytics/events` 发送每批最多 25 条事件；虽然当前事实全集最多只有 5 条（一次初始化和四个平台各一次已验证投递），spool 仍以 25 为硬上限并与 Server 批量合同一致。网络、HTTP、响应解析失败、Server 明确拒绝或未确认的事件都 fail closed 留在本地，不改变原命令输出、退出码或招聘工作流状态。`JOBAGENT_ANALYTICS_DISABLED=1`、`DO_NOT_TRACK=1` 可选择退出，`JOBAGENT_ANALYTICS_KILL_SWITCH=1` 可关闭采集与发送；三者都保留已有 spool，不做隐式清理。

`jobagent_initialized` 是 instrumentation coverage 启用后，该账户首次成功在线验证并完成 owner 落盘的可观测初始化锚点；成功的 `init`、legacy `account bind` 或新账户 `account switch` 都可补齐同一个幂等事实。它不代表早于该埋点版本的历史首次安装时间；失败、只读 `account status` 与 `init --no-verify` 均不得记录。

## 启动顺序

每条 CLI 命令按以下顺序执行：

1. 验证签名版本策略；已有原生执行意图未完成时延后程序替换，先按同一任务完成或只读核验。无在途冲突时受管安装按政策完成客户端更新；仅在真实发现新版时输出 `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed`，更新成功后自动恢复原命令。
2. 检测 `client_version`、协议版本和状态迁移版本。
3. 对目标安装目录的原生工作账本执行只读预检：未决副作用、不可读或不支持的 schema 均阻断任何清理、迁移、归档和升级完成标记写入。随后检查是否存在仍存活的 Job Agent 进程；有则保留原迁移版本、记录 `migration_pending=true` 并停止迁移。
4. 清理可重建状态、迁移旧 schema、按协议边界归档运行时决策；旧版待发送平台必须回到完整预览与最终确认，不能继承自动发送权限。
5. 校验 API Key 与画像兼容性。
6. 从云端取得不可枚举的稳定 `account_ref`，校验本地业务状态归属；旧状态未认领或账户不匹配时阻断。
7. 无冲突才允许 Boss、猎聘、智联和 51Job 的真实平台命令进入 dispatch。

`account`、`init`、`doctor`、`upgrade-check`、`platforms` 和 `update` 等恢复或只读命令在冲突期间仍可运行。画像属于账户业务状态，`resume analyze` 必须等 owner 归属问题解决后再执行。平台自动化命令收到 `client_upgrade_required` 后，宿主 Agent 必须执行响应中的 `next_suggested`，不可绕过检查。

若唯一冲突为 `native_browser_work_inflight`，`work next/begin/submit/status` 可继续原任务的恢复协议；`work recover` 仅能对客户端明确允许的只读采集任务执行下述恢复，不能借此重新发放已记录副作用的许可。账户与画像校验仍必需。Key、画像或账本兼容性冲突不能用 work 命令豁免。账本不可读时执行只读 `jobagent upgrade-check` 查看冲突，保留原文件并使用兼容客户端处理；不得删除账本来解除阻断。

## 发布门槛

任何改变 CLI 版本、协议、持久化 schema、路径或默认行为的发布都必须回答并验证：

1. 上一公开版本留下了哪些文件和锁？
2. 每一项是 preserve、migrate、clear、archive 还是 block？
3. 新迁移是否具备版本号，并且第二次运行无副作用？
4. 活进程中断迁移后，下一次启动能否继续完成？
5. audit、登录态、API Key、画像和用户偏好是否保持不变？
6. 协议变化是否只归档不再可信的运行时决策？
7. `upgrade-check` 是否一次返回全部冲突和一个可执行的首要恢复动作？
8. 是否同时跑过上一公开版本夹具、当前版本夹具和损坏状态夹具？

禁止用“让用户删除 `~/.jobagent` 后重装”作为正常升级方案。只有在已经确认具体文件不可恢复、完成备份并获得用户明确同意后，才可对单个文件执行人工修复。

## 验收标准

### 应用范围原生窗口兼容（同协议扩展）

- **preserve**：原生账本 schema、原任务定义、work ID、nonce、观察次数、轮次、简历绑定和账户状态不迁移。旧待绑定任务显示当前完整回执契约，但不改写存储的 specification。已接受的回执不可变。
- **typed scope**：新增 `window_reference_kind=app_scoped_window`，引用值为宿主真实应用引用，不冒充窗口句柄；绑定时持久化类型。每次非暂停回执要求新鲜的 `window_context`，应用引用一致、当前窗口标题、窗口选择已核验及实际选择证据。标题不作为固定身份。旧窗口 ID/handle 与未标类型的既有绑定兼容，但已标应用范围的任务不得省略证据降级。
- **verify before action**：此模式保证每步重新选定同 profile/account 上下文，不承诺固定物理窗口。每次 UI 操作前读取新鲜原生状态；任务允许的选窗、选标签或导航至指定官方 URL 可作为定位准备，随后再次观察。采集、岗位/回执核验与招聘动作前核验窗口、profile、官方页面与已绑定账号；仍有歧义即暂停。缺少窗口枚举不能推断只有一窗。成功回执的事后校验不能替代点击前检查。投递授权、精确岗位身份和不可重复发送约束不变。
- **diagnostics**：绑定能力字段错误列出 `invalid_fields` 并保留原 work；缺少窗口 ID 不等于能力不可用。真实能力不可用时仅提交最小暂停证据，不填造成功字段。
- **upgrade evidence**：旧版正在处理的只读绑定也可能延后自动升级。必须以实际上一版受管安装验证更新入口与状态保留，不能把新代码测试当作旧版自动升级成功；未完成真实宿主从绑定到采集的验证前不得声称客户路径已修复。

### 只读采集会话恢复（同协议扩展）

- **preserve**：BrowserWork schema 不迁移，旧回执、nonce 与观察次数不重写；账户、API Key、Chrome profile/cookies、逻辑 session ID、round、request、Discover、已完成采集页、候选、签名决策、预览授权和 audit 全部保留。
- **explicit recovery**：仅当前未完成、`side_effect=false` 且不含 `delivery_source` 的 `collect_search_page` 可在用户明确同意恢复范围后执行 `jobagent work recover --work-id ID --confirm-recover`。旧版已由用户明确取消的源采集任务，只有仍对应原 pending request/checkpoint 的下一未完成页时也可恢复；已采集完成或已完成决策的任务不可恢复。该命令结束旧只读任务并创建 `recover_session` 只读任务；它不把旧任务改成采集成功，也不重置旧任务次数。重复恢复返回既有恢复任务，不重置其观察次数，也不重新开放已取消的恢复任务。
- **verify before resume**：新任务必须通过当前宿主的真实窗口/profile 观察和同一已绑定平台账号的独立证据校验，之后才允许更新实际 `window_reference` / `group_reference` 并续接采集。优先使用宿主提供的稳定窗口 ID/handle；前景标签或动态标题变化不等于窗口丢失，不得抄写旧标题伪造匹配。新任务未成功、profile/account 不一致或证据不足时保留请求并阻断采集。
- **block**：投递来源任务、已有副作用意图，以及不符合上述源采集取消例外的已终结任务不适用；不能产生新的发送许可。其他账户、画像、签名和账本冲突仍失败关闭。恢复不创建新付费请求；后续云决策的正常计费合同不变，不能承诺整轮免费。
- **no automatic reset**：升级本身不执行恢复、不清空会话或轮次。恢复范围只需一次明确确认；宿主负责当前窗口和页面排查，在排查中遇到真实权限不可用、会话/账号仍不明确或登录验证需要用户时交接。未知的 `noWindowsAvailable` 仍需保留为宿主诊断，不能宣称已修复或无限循环恢复。

### 0.6.13 恢复预检与 0.6.12 绑定保留修正（同协议扩展）

- **preserve**：恢复预检失败不得清除或改写原简历绑定、round、pending request、checkpoint、候选或 BrowserWork。材料网络/服务错误保留 `resume_binding_material_unavailable` 与 `recovery_cause`，不能误报成 `native_recovery_not_current`；真正源页不匹配仍阻断。按 `retryable` 区分可重试故障和必须先处理的前置条件，后者处理后使用返回的原 work 恢复命令，不改用平台 Discover。
- **verify before restore**：兼容 `0.6.12` 失败预检误清本地 `resume_binding` 的状态，只能从原签名 SearchPlan 的完整冻结快照取得原绑定，并逐项验证账户、round、request、Discover、session、已确认意图与 profile 摘要，再读取服务端当前同一材料进行一致性校验。预检只在内存使用该快照；成功恢复回执续行时再次校验一致后才允许补回。原签名缺少绑定、签名不可信、已有绑定不一致或当前材料不可用时不得推断、换绑或从本地 profile 补造。
- **committed receipt**：成功 UI 回执已在账本提交后，若材料复校阻断，错误必须返回 `recovery_receipt_saved=true`、`browser_replay_permitted=false`，不得返回或转述 `recovery_state_changed=false`。已保存回执与未完成续行分开报告；前置条件解决后按返回的原 `work recover` 续行，不重发 `work begin`、不重做浏览器动作，也不创建新回执掩盖中断。
- **explicit new material**：原绑定为 stale/released 时返回 `recovery_requires_new_round=true`，不能把当前新修订或另一材料放入旧签名请求。宿主需取得一次明确业务确认，先读 `round status` 与 `work status`。若原只读源任务仍 open，仅当返回同一 `collect_search_page`、`side_effect=false`、无 `delivery_source` 且提供 `cancel_command` 时，在该结束旧轮确认范围内执行此命令；确认 `ok=true` / `browser_work_cancelled` 后再次检查任务状态。不得为解锁轮次批量取消其他任务或未确定投递；无安全取消入口时按返回的核验流程处理。原源任务关闭且无其他 open work 后，按 `round status` 的当前平台依序执行既有 `round skip --platform <current_platform> --confirm-skip`，每次确认成功，直至 `workflow.workflow_complete=true`；之后运行 `round start`，按用户选择完成当前简历、目标岗位和城市交互。已有同范围确认继续有效，技术恢复确认本身不授权换材料或结束整轮。
- **archive, no silent reanalysis**：上述显式新轮路径由 CLI 按既有规则归档旧 pending 状态；原轮历史、候选、签名、回执及审计保留，不将旧候选或授权冒充新轮结果。不得为本次换轮自动重做简历分析，也不得承诺新轮免费；新云端操作按原计费合同执行。旧请求未恢复、旧轮被跳过与新轮开始必须分别报告。执行说明见 [Agent 工作流](./agent-onboarding.md#native-recovery-material)。

### 原生技术暂停分类（同协议扩展）

- **preserve**：现有 BrowserWork schema、nonce、绑定、观察次数、账户、轮次、请求、候选、预览授权和审计均不迁移、不清理。旧用户暂停回执仍可读取，不自动改写其原因。
- **additive**：新回执可声明 `requires_technical_recovery=true`，原因限定为 `job_identity_unknown` 或 `page_state_unknown`；结果仍为既有 `uncertain`，账本仍处于 `reconcile_only`。读取状态必须明确技术暂停，不能改报需要登录或关闭窗口。
- **block**：已有副作用意图永不重新获得 `execute_once`；只读任务仍受原观察次数上限约束，终结任务不重新开放。技术暂停不产生成功记录、不推进采集、不发起决策或重复计费。宿主只能提交当前客户端明确声明的回执合同。

### 0.5.44 → 0.6.0 原生执行器迁移

- **migrate**：状态迁移 v8、round schema v4。活动轮次新增 `browser_executor=codex_native`；旧 `browser_session_id` 记为 `legacy_browser_session_id`，当前会话标为 `native-unbound`、`native_session=null`，随后由宿主只读确认实际窗口并绑定。旧登录证据保留但不自动证明新会话已登录。新轮次同样从原生未绑定状态开始。
- **preserve**：账户、API Key、画像、round ID、意图、request/Discover、采集断点、候选、签名决策、完整预览、有效授权、交互、原生投递进度、历史轮次、四平台审计及 Chrome profile/cookies 全部保留；已存在的原生会话绑定不清空。v7 → v8 同协议只修改当前轮次执行器元数据与升级标记，不清理已有映射或业务文件。
- **block**：目标安装目录 `state/browser-work.sqlite3` 以只读模式打开并核对 schema；`side_effect=1` 且状态为 `intent_recorded` 或 `reconcile_only` 时返回 `native_browser_work_inflight` 和 `jobagent work status`。不受另一目录的默认账本影响，不因超时、旧 PID 消失或没有最终回执就清除意图。账本损坏、不可读、未知 schema 同样失败关闭。
- **no mutation while blocked**：不清缓存、不删除锁、不迁移轮次、不归档决策、不写新版完成标记。恢复读取旧轮次时也不隐式迁移其绑定。原任务回执明确结束后，下一次启动才执行幂等迁移。
- **boundaries**：兼容的预览和授权不因执行器切换自动失效；发送入口仍重新验证签名、账户、轮次与最终确认。只读的 `round status` 返回执行器和既有 `native_delivery`，不将未确定项提升为成功。协议只协调产品任务，不保证外部网页 exactly-once，也不能拦截宿主在协议外的 UI 行为。

### 0.5.43 → 0.5.44 猎聘采集断点

- **preserve**：账户、API Key、Chrome profile/登录态、画像、活动轮次、已有决策、预览授权和 audit 原地保留；上一版本 `pending-start.json` schema v1 原请求不删除、不替换。
- **migrate**：仅首次完整验证并完成一个查询页后，原子写入 schema v2 `pending-start.json` 的可选 `collection` v1 字段；包含原签名 SearchPlan、候选、已完成查询页和已耗尽查询。重复保存相同断点内容不变，不需要批量启动迁移。
- **block**：恢复时重新验签并校验账户、本轮、画像、意图、请求与计划语义；过期计划只允许原请求/Discover 的零费用续签，查询或上限变化、签名失效、损坏断点均保留数据并停止。已有断点不能被自动画像重绑清除。
- **archive**：用户显式创建另一轮后，旧轮的部分采集状态归档；不同账户不能触发此替换。完成采集后先持久化同一 pending decision，再清除 start 断点。
- **clear**：本次不新增任何浏览器或账户状态清理。旧版本未保存逐页候选，因此不能凭日志伪造历史断点；保留原 request，首次新版执行正常验证计划与页面，之后支持逐页恢复。
- 猎聘验证码返回 `liepin_verification_required`、明确 `user_prompt` 与 `requires_user_action=true`；用户完成验证后才执行返回的原 Discover 命令。无结果/末页证据仅结束当前已验证查询，解析空列表本身不等于查询耗尽。

- 旧安装首次启动：自动迁移一次，报告 `cleared`、`migrated`、`archived` 和 `conflicts`。
- 真实新版：受管安装按阶段输出版本号与状态，成功后原命令自动恢复；当前已是最新版时不输出升级事件。
- 旧事件协议兼容：从尚不具备阶段事件的旧客户端升级后，新进程至少补发一次 `client_update_completed` 和 `client_command_resumed`，后续版本升级输出完整四阶段。
- 同版本再次启动：`upgrade_detected=false`，不重复删除或归档。
- 活锁场景：不修改运行时状态，返回 `active_process_lock`；进程结束后自动续做。
- 过期 Key / 不兼容画像：平台 dispatch 不执行，恢复命令可执行。
- 协议变化：旧 discoveries 可追溯归档，所有 audit 原地保留。
- `0.5.7 -> 0.5.8`：签名决策、候选列表、用户提升项、画像、账户与 audit 原地保留；旧预览/授权失效，未发送平台重新展示清单并等待确认，不产生新 Discover 费用。
- `0.5.8 -> 0.5.9`：账户绑定原地升级为不保存明文 Key 的离线证明；只有未更换过的绑定凭据可安全迁移。画像、轮次、签名决策、候选、预览、授权、Chrome 登录态与 audit 全部保留。进行中的 Discover start 和浏览器采集失败会持久化并复用原 `request_id`，不创建新轮次或新增费用。智联旧城市缓存无需删除；经多源验证的新映射会原子升级为带证据来源与验证时间的 schema v2。
- `0.5.9 -> 0.5.10`：不迁移或清理任何持久状态。升级只替换智联会话证据裁决器：常驻通用登录入口降为弱证据，独立账户导航与简历/投递活动证据达到阈值时继续当前登录会话；强登录界面与强账户证据冲突时仍 fail closed。现有 profile、轮次、同一 Discover `request_id`、候选、计费状态、Chrome 登录态与 audit 全部保留。
- `0.5.10 -> 0.5.11`：不迁移或清理任何持久状态。已保存的 Discover `request_id`、`discover_id` 与本地候选原样保留；有效签名 SearchPlan 跨过 TTL 后由 Server 针对同一请求重新签发，续签本身不收费，也不重新采集。签名篡改、账户错配、画像或意图上下文变化不能借过期恢复绕过校验，必须 fail closed。
- `0.5.11 -> 0.5.12`：不迁移或清理任何持久状态。智联最近一次成功登录检查以短时凭证绑定当前 round、platform 与 managed Chrome session；结果页缺少首页账户区时可作为辅助证据，凭证过期、轮次或浏览器会话变化即失效，强登录表单或验证界面始终优先。采集失败保留该凭证与原 Discover `request_id`，页面已就绪但岗位 DOM 无法解析时返回 `zhilian_job_cards_not_found`、`retryable=true`、`no_charge=true`，不新建轮次、不重复登录、不重复收费。
- `0.5.26 -> 0.5.27`：若用户在空城市的浏览器采集失败后明确补充了目标城市，且活动轮次尚未产生候选、签名决策、预览、授权或投递证据，则状态迁移 v7 原地更新该轮次的画像摘要，保留 round ID、账户、API Key、画像、Chrome profile、近期登录凭证与 audit，只清除与旧画像绑定且尚未收费的 Discover start 上下文，并从当前平台 Discover 继续。已有任何候选或投递进度时不自动重绑，返回冲突并保持原状态。
- `0.5.27 -> 0.5.28`：不迁移或清理账户、API Key、画像、活动 round、近期猎聘登录凭证、Chrome profile、保留的 SearchPlan request 或 audit。新猎聘城市缓存独立创建，只有城市控件、页面元信息/标题、原始可读关键词和真实结果面交叉验证成功后才写入；旧页面或冲突证据不写缓存。原 `liepin_city_code_not_found` 失败保持未收费，升级后直接执行 `jobagent liepin discover` 复用同一 `request_id`。
- `0.5.28 -> 0.5.29`：不迁移或清理任何账户业务状态。升级只补齐猎聘可读城市路由：当前可信结果页无城市链接时可有界打开官方通用搜索结果页，数字码缺失时保持在经过路由变化、城市、查询词和结果面交叉验证的 `/city-<slug>/zhaopin/` 路径分页；城市首页推荐不进入候选，后续数字码只有独立验证成功才写入现有 cache schema。API Key、账户绑定、画像、活动 round、近期登录凭证、Chrome profile、同一未收费 SearchPlan request 与 audit 均原地保留，升级后继续精确的 `jobagent liepin discover`。
- `0.5.29 -> 0.5.30`：不迁移或清理任何账户业务状态。猎聘已授权发送不再从首个岗位城市重建搜索 URL，也不要求可读城市路由必须有数值 code；客户端逐条打开已审核、已签名的岗位详情，并在任何平台动作前核对实际详情路由。真实登录墙仍按原协议请求登录，城市/详情导航失败不再伪装为登录。API Key、账户绑定、画像、活动 round、Chrome profile、登录态、签名 decision、review 文件、delivery preview、authorization 与 audit 全部原地保留；升级后继续原精确 send 命令，不重新 Discover、review、确认或收费。
- `0.5.30 -> 0.5.31`：不迁移或清理任何账户业务状态。智联登录证据在本地重新归一化个人中心导航、账号存在、简历管理与历史投递/面试活动；常驻通用登录入口继续只算弱证据，真实登录路由、凭据表单和登录验证仍失败关闭，强登录与强账户冲突仍返回 unknown。API Key、账户绑定、画像、活动 round、Boss/猎聘既有 audit、Chrome profile、登录态、签名 decision、preview、authorization 与当前智联阶段全部原地保留；升级后先执行只读 `jobagent zhilian login --check`，不重新登录、Discover 或收费。
- `0.5.31 -> 0.5.32`：不迁移或清理任何账户业务状态。智联搜索结果必须同时验证原始可读查询与目标城市；停留在旧城市结果页时，客户端从页面公开可读入口动态发现并独立验证目标城市路由，再重新提交原查询。旧数值城市码不能阻止目标城市切换，新的数值码仍只在页面标题、可见城市与岗位卡片等多源证据一致后写入既有缓存。API Key、账户绑定、画像、专用 Chrome profile、登录态、活动 round、既有 audit 与同一未收费 Discover request 全部原地保留；升级后继续精确的 `jobagent zhilian discover`，不新建轮次、不重复登录、不重新计费。
- `0.5.32 -> 0.5.33`：不迁移或清理任何账户业务状态。升级补齐智联当前公开页面的完整城市转换链：从旧城市结果页动态进入官方城市目录，独立验证可读目标城市首页，再提交原始可读查询并仅在目标城市与查询双重验证后采集；进入新页面前会清除旧页瞬时证据，真实登录表单或验证挑战仍失败关闭。API Key、账户绑定、画像、专用 Chrome profile、登录态、活动 round、既有 audit 与同一未收费 Discover request 全部原地保留；升级后继续精确的 `jobagent zhilian discover`，不新建轮次、不重复登录、不重新计费。
- `0.5.33 -> 0.5.34`：不迁移或清理任何账户业务状态。若已独立发现的智联目标城市路由在有效登录会话中回落到通用首页，客户端会在同一受账户绑定的浏览器会话内有界操作可见城市控件，确认目标城市后重新提交原始可读查询，并仅在城市、查询和真实结果面同时验证后采集。旧城市数值码、首页推荐和目录回跳均不能冒充目标结果；真实登录表单或验证挑战仍失败关闭。API Key、账户绑定、画像、专用 Chrome profile、登录态、活动 round、既有 audit 与同一未收费 Discover request 全部原地保留；升级后继续精确的 `jobagent zhilian discover`，不新建轮次、不重复登录、不重新计费。
- 损坏状态：原文件可追溯归档，后续命令不因 JSON 解析错误崩溃。
- Release archive 校验固定 `tar.umask=002`，忽略系统级 Git 配置、全局 attributes 和 replace refs；发布机与客户机必须对同一 commit 得到相同 SHA256。
- 旧客户端若返回 `release artifact hash mismatch`，不得关闭校验或删除 `~/.jobagent`。重新运行官方安装器一次以修复受管仓库配置，并保留账户状态、浏览器登录、画像、轮次和审计。

### 0.6.15 → 0.6.16 HTTPS 依赖支持

- **preserve**：API Key、账户归属、所有 BrowserWork/nonce/回执、简历、轮次、预览授权、审计及 Chrome profile 原样保留；无 schema 或状态迁移。
- **additive**：安装环境增加 certifi 根证书依赖；只在进程内补充默认 TLS 信任，不写系统钥匙串或 shell 配置。用户显式 SSL_CERT_FILE/SSL_CERT_DIR 保持权威。安装器附加无账号 HTTPS 检查，普通 onboarding 仍离线。
- **block**：过期、域名不符、未知颁发者或损坏自定义证书仍阻断，不因证书失败重试业务请求或使用离线账户证明。doctor tls 不更新程序、不迁移或写业务状态。

### TLS and exhausted native observation continuity

TLS repair preserves every work ID, nonce, account binding and observation count.
A successful `doctor tls` offers `doctor env` as a safe continuation, without
accessing business state during the TLS probe itself. The original task's actual
permissions remain authoritative after that check.

Native responses add a `recovery` object for exhausted read-only tasks. This is
an additive presentation contract, not a ledger migration or attempt reset.
Eligible search-page collection exposes the existing explicitly confirmed
`work recover` path consistently from next/status and rejected begin responses.
Other exhausted tasks retain `receipt_only`; recovery tasks and delivery work
cannot use this addition to obtain another execution budget. The last newly
issued observation permit remains usable for that observation; later reads
never reissue it.
