# claudeMain 整体优化计划（2026-10-05）

基线：`main@8197791e`。分支：`claudeMain`，工作副本 `.worktrees/claudeMain`。
范围：代码与本地验证；不推送、不部署，设备验收另行授权。

## 诊断依据

| 现象 | 证据 | 结论 |
| --- | --- | --- |
| 单消费者被长任务占住 | 2026-10-05 BacktotheDate 占用约 26 分钟，期间无写屏（`outputs/runtime-status-20261005-2038`） | 协作式取消失效时没有兜底 |
| 插件 HTTP 不受任务截止约束 | `utils/http_client.py` 在调用方不传 `context=` 时为每个请求新建永不取消的上下文；插件里有 103 处直接调用 `get_http_session()` | deadline 只约束少数显式传上下文的插件 |
| 取消被吞 | `TaskCancelled(RuntimeError)`；插件中 716 处宽泛处理器未先放行取消 | 新代码会继续积累同类缺陷 |
| 健康检查只报警、不恢复 | `health.py` 计算 `scheduler_stalled`，没有恢复动作 | 冻结时长没有上限 |
| 协调器回到单体 | `refresh_task.py` 从 7 月 1 日的 1,389 行增长到 10,161 行；上限 10,260；`_select_independent_refresh_command` 647/650 行 | 下一次调度修复就会触碰上限 |
| 插件特例进入核心 | `refresh_task.py` 中出现 129 次 `weather`；weather、sports、ticketmaster 三套结构几乎相同的 liveness 方法 | 应改由 manifest 能力声明 |
| 运维工具碎片化 | 每次任务各写 deploy/accept/audit 脚本；验收工具写死实例数（ERR-20260904-ACCEPT） | 审计方法停留在手工 |

## 迭代

### 迭代 1：取消语义与卡死兜底（已完成第 1-3 项）

1. 插件 HTTP 跟随任务截止：`TimeoutSession` 与 `HttpClient` 在没有显式上下文时继承当前绑定的任务上下文。截止后立即拒绝新请求，单次超时不超过剩余时间。显式上下文仍然优先。
2. 超时升级：新增 `runtime/overrun_recovery.py`，由重启监控线程判断。当前命令超过截止时间加 240 秒宽限（`refresh_overrun_grace_seconds`）后，请求受监督的进程替换，复用 exit 75、15 秒强制退出和恢复账本。账本限定 1 小时内最多一次（`refresh_overrun_min_interval_seconds`），防止陷入重启循环。
3. 取消吞没计数门禁：`tools/check_architecture.py` 统计插件中未先放行 `TaskCancelled` 的宽泛处理器，上限 716，只能下降。
4. 评估项：`TaskCancelled` 改为继承 `BaseException`。需要先处理 HTTP 边界（`manual_update` 会把取消抛给 Flask，而 waitress 只捕获 `Exception`），以及核心中 6 处清理后重抛的处理器。本迭代不做全局切换。

验收：相关单元与集成测试全部通过，全量测试无新增失败，ruff、架构门禁、mypy 通过。设备验收要求：自然运行 48 小时，在部署窗口之外写屏间隔不超过 2 倍轮播间隔；若发生超时恢复，账本中能看到 `refresh_worker_overrun`。

### 迭代 2：调度核心瘦身

- 已完成：Sports 与 Ticketmaster 的 quiet window 状态机合并为 `runtime/liveness_window.py`。
- 已完成：`_select_independent_refresh_command` 拆出 burst liveness 决策、两处资源余量不足时的延后，以及原先重复 5 次的后台 DATA 命令构造；函数从 647 行降到 459 行，门禁收紧为 480 行。
- 结果：`refresh_task.py` 从 10,173 行降到 9,939 行，文件上限收紧为 10,040 行。
- 已完成（2026-10-06）：`_execute_queue_entry` 拆为四道准入关卡（`_reject_before_execution` 及三个插件专用方法）和执行与结果分类（`_execute_and_finish`），协调函数从 614 行降到 74 行，新增 120 行门禁。
- 已完成（2026-10-06）：Weather quiet window 移入 `runtime/weather_liveness.py` 的 `WeatherQuietWindow`，资源余量、时间解析、内存维护和调度唤醒通过回调注入。`refresh_task.py` 降到 9,830 行，文件上限收紧为 9,930 行。
- 不做：`runtime/execution_policy.py` 的插件名单是刻意集中审核、失败即关闭的设计，保持原样。

### 迭代 3：运维与验收工具化

- 已完成：两个验收工具都按当前 playlist 的实际实例数执行（`--expected-instances` 可选，用于锁定已审阅的数量），新增 `--display-only`。
- 已完成（2026-10-06）：`tools/runtime_audit.py` 自动做两次观测（默认间隔 330 秒），按取数（data 通道）、渲染（presentation 通道）、写屏（显示提交与日志写屏次数）三个阶段给出结论，并汇总失败命令和单条命令内存峰值。首次在设备上运行结论为 ok。
- 待决定：公开 readyz 是否只返回错误码（不暴露配置）。

### 迭代 4：内存（以设备实测为准）

- 先测出各插件的峰值 RSS 并排名，再处理前三名。候选方向：Weather 的 Chromium 渲染路径、Sports 各联赛的数据准备与绘制分离、JPEG `draft` 解码。
- 2026-10-06 实测（设备日志 26.6 小时、14 个进程，`outputs/memory-ranking-20261006/`）：
  - 第一名 telegram_digest：进程内首次运行时 RSS 最多上涨 116 MB（其中 Telethon 导入 33 MB），之后常驻约 45 MB 不释放。已改为在短命子进程中执行账号抓取，主进程不再导入 Telethon。
  - daily_wiki_page 的峰值只出现在每个进程首次运行时（约 43 MB）；设备上用真实字体和测试数据渲染只需 12～15 MB，原因未能从现有日志定位。
  - 其余排名不可靠：VmHWM 是进程级的只增不减值，加上内存维护按间隔执行，峰值会被算到碰巧之后上报的插件头上（例如 Steam 被算上了 daily_ai_news 留下的 RSS）。
  - 为此新增 `runtime/command_memory.py`：每条命令开始时通过 `/proc/self/clear_refs` 重置 VmHWM，日志新增 `command_peak_mb`。下一步：部署后积累数小时 `command_peak_mb`，用 `tools/runtime_audit.py` 或按插件汇总，按准确排名处理第二、三名。

### 持续项

- 修改插件时，把它重复实现的绘制工具函数迁到公共库。
- 每修复一处取消吞没，就下调计数上限。
- mypy 检查范围随新拆出的模块一起扩大。
