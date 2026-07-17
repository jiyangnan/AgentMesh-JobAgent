# Job Agent 客户端升级契约

本文定义公开 CLI 从历史版本原地升级时，哪些本地资产必须保留、哪些状态可以自动清理、哪些数据需要迁移，以及什么情况必须阻断平台操作。它是客户端状态兼容性的唯一工程依据。

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
| `state/support_state.json` | 保留 | 首次投递后的单次提示状态不得因升级重置 |
| `state/current_round.json` | 按 schema 迁移 | 迁移记录来源 schema 和原因；含义不明确的旧平台状态重置为安全的待执行状态；损坏 JSON 保留到 archive 后重建 |
| `state/rounds/` | 保留 | 历史轮次不覆盖、不删除 |
| `state/discoveries/` | 同协议保留，协议变化时归档 | 归档到 `state/archive/`，不得连同 audit 一起删除 |
| release manifest cache | 自动清理 | 新版本重新获取并验证签名策略 |
| platform tab / browser-session marker | 自动清理 | 只清理可重建的 CDP 映射，不触碰 Chrome cookies/profile |
| last doctor / probe 输出 | 自动清理 | 旧诊断结论不应冒充新版状态 |
| activity / browser / update lock | 死亡进程自动清理 | 锁所属 PID 存活时阻断迁移；不得抢占真实运行中的命令 |
| logs | 保留 | 用于跨版本排障；不得写入 API Key 等秘密 |
| 用户配置 | 校验，不覆盖 | 新版默认值不能静默覆盖用户配置；不兼容项必须显式报告 |

## 启动顺序

每条 CLI 命令按以下顺序执行：

1. 验证签名版本策略，受管安装按政策完成客户端更新；仅在真实发现新版时输出 `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed`，更新成功后自动恢复原命令。
2. 检测 `client_version`、协议版本和状态迁移版本。
3. 检查是否存在仍存活的 Job Agent 进程；有则记录 `migration_pending=true` 并停止迁移。
4. 清理可重建状态、迁移旧 schema、按协议边界归档运行时决策。
5. 校验 API Key 与画像兼容性。
6. 从云端取得不可枚举的稳定 `account_ref`，校验本地业务状态归属；旧状态未认领或账户不匹配时阻断。
7. 无冲突才允许 Boss、猎聘、智联和 51Job 的真实平台命令进入 dispatch。

`account`、`init`、`doctor`、`upgrade-check`、`platforms` 和 `update` 等恢复或只读命令在冲突期间仍可运行。画像属于账户业务状态，`resume analyze` 必须等 owner 归属问题解决后再执行。平台自动化命令收到 `client_upgrade_required` 后，宿主 Agent 必须执行响应中的 `next_suggested`，不可绕过检查。

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

- 旧安装首次启动：自动迁移一次，报告 `cleared`、`migrated`、`archived` 和 `conflicts`。
- 真实新版：受管安装按阶段输出版本号与状态，成功后原命令自动恢复；当前已是最新版时不输出升级事件。
- 旧事件协议兼容：从尚不具备阶段事件的旧客户端升级后，新进程至少补发一次 `client_update_completed` 和 `client_command_resumed`，后续版本升级输出完整四阶段。
- 同版本再次启动：`upgrade_detected=false`，不重复删除或归档。
- 活锁场景：不修改运行时状态，返回 `active_process_lock`；进程结束后自动续做。
- 过期 Key / 不兼容画像：平台 dispatch 不执行，恢复命令可执行。
- 协议变化：旧 discoveries 可追溯归档，所有 audit 原地保留。
- 损坏状态：原文件可追溯归档，后续命令不因 JSON 解析错误崩溃。
