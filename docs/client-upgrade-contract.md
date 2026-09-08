# Job Agent 客户端升级契约

本文定义公开 CLI 从历史版本原地升级时，哪些本地资产必须保留、哪些状态可以自动清理、哪些数据需要迁移，以及什么情况必须阻断平台操作。它是客户端状态兼容性的唯一工程依据。

关联文档：[用户指南](../README.md)、[Agent 工作流](./agent-onboarding.md)、[Codex 原生操作 Skill](../skills/codex-job-agent/SKILL.md)。

## 目标

已安装旧版的客户升级后，应当直接得到一个可继续工作的客户端，而不是由宿主 Agent 猜测是否需要清缓存、重登平台或删除目录。升级过程必须满足：

1. 不丢 API Key、招聘网站登录态、简历画像、审计记录和用户偏好。
2. 自动清理只对可重建的临时状态生效。
3. 旧 schema 有明确、幂等的数据迁移。
4. 无法安全修复的冲突阻断真实平台动作，并返回机器可读恢复命令。
5. 同一迁移重复运行不会再次清理、归档或改写用户数据。

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

若唯一冲突为 `native_browser_work_inflight`，`work next/begin/submit/status` 可继续原任务的恢复协议，不能借此重新发放已记录副作用的许可；账户与画像校验仍必需。Key、画像或账本兼容性冲突不能用 work 命令豁免。账本不可读时执行只读 `jobagent upgrade-check` 查看冲突，保留原文件并使用兼容客户端处理；不得删除账本来解除阻断。

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
