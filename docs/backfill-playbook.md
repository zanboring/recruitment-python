# 岗位详情回填 · 运行手册

> 适用脚本：`scripts/backfill_detail.py`，只回填 `job_desc` / `detail_html` / `publish_time` 三列（脚本 docstring :11-15）。
> 口径：完整正文 = `length(job_desc) > 200`，待回填 = `<= 200`，两者互补（16 + 539 = 555，baidu-REPORT §1）。
> 本手册已按本轮落地实现更新：F1（codebuddy）、F4/F5（trae）、方案（qoder）、基线（baidu）均已交付并集成端实测通过。

## 1. 跑之前（准入条件）

逐条成立再开跑；任一不满足先处理。

1. **工作区干净、分支正确**：`git status --porcelain` 无输出；`git branch --show-current` 与本轮分支一致。
2. **journal_mode 必须是 delete，不是 wal**：打开主库执行 `PRAGMA journal_mode;`，期望返回 `delete`。依据：WAL 在本机并发写下偶发 readonly、已撤销（`app/database.py:137-148`）；启动时 `ensure_delete_journal_mode` 幂等切回（`app/database.py:157-173`，`app/main.py:54-59`）。
3. **先在主库固化基线**：把 baidu-REPORT §1 的 7 条 SQL 在主库跑一遍记录现值；快照为 11-11 所拍，不能直接当验收基准（baidu-REPORT「需主库确认」）。
4. **已有可用备份**：脚本非 dry-run 开跑前自动调用 `sqlite3.Connection.backup()` 做在线备份到同目录 `<库名>.bak-<时间戳>`，随后 `PRAGMA integrity_check`，不通过即中止（`scripts/backfill_detail.py:183-215`）。校验通过的备份**成功失败都保留**——它是本批写入的唯一回滚锚点；校验失败或复制中途异常的半截文件会被**立刻删除**（留着坏副本比没有更危险）。开跑前仍建议手动留一份独立备份作为第二重保险。
5. **止损参数本轮修复后真的有效**：`--max-consecutive-blocked` 默认 5（`backfill_detail.py:394-396`）。F1 修复已由 codebuddy 交付：阈值语义为「连续 5 条岗位最终分类为 `waf`/`login_wall`」，`network`/`parse`/`no_field` 打断连续计数（codebuddy-REPORT §1.2；§2.1 实测阈值 3 即停、退出码 4）。该改动尚在 codebuddy 分支、未并入本只读副本，集成合并后以代码为准。
6. **节流不要调零**：同域名最小间隔默认沿用 `settings.crawl_domain_min_interval`（20 秒，`app/config.py:372`）；`--interval` 只在有明确依据时改（`backfill_detail.py:389-392`）。

## 2. 怎么跑（命令 + 判读）

### 2.1 先在副本上试跑

把主库复制一份到临时副本（路径从主库推导，不写死本机位置），用 `--db` 指向副本；副本状态文件用 `--state` 与主库隔离（`backfill_detail.py:387-388`）。

```bash
# 只抓不写、不备份（backfill_detail.py:385,:246-248）
python scripts/backfill_detail.py --db data/test_copy.db --state data/test_copy_state.json --limit 5 --dry-run
# 副本上试写 5 条，确认写入与状态文件行为
python scripts/backfill_detail.py --db data/test_copy.db --state data/test_copy_state.json --limit 5
```

### 2.2 正式跑

```bash
python scripts/backfill_detail.py --limit 20
```

关键参数（`backfill_detail.py:382-405`）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--limit` | 20 | 本批最多处理条数 |
| `--dry-run` | 关 | 只抓不写、不备份 |
| `--resume` | 关 | 跳过状态文件 `ok` 列表里的岗位 |
| `--db` | `data/recsys.db` | 库路径，相对仓库根解析 |
| `--state` | `data/backfill_detail_state.json` | 续跑状态文件 |
| `--max-consecutive-blocked` | 5 | 连续 N 条被拦（WAF/登录墙）停本批，退出码 4 |
| `--batch-size` / `--cooldown` | 50 / 30s | 每 N 条批间冷却 |

### 2.3 日志判读

- **正常**：`[i/N] ok id=... source=... desc_len=...`；每 10 条一行进度；收尾 `完成：成功 / 失败 / 跳过`、`job_status 校验：...一致`、`detail_html 覆盖：x/555`（`backfill_detail.py:276-281,:318-324,:343-376`）。
- **该停**：`[STOP] 连续 N 条被站点拦截`（退出码 4，`:308-316,:377`；F1 修复后只把 `waf`/`login_wall` 计入连续计数，codebuddy-REPORT §1.2）；连续多行 `fail ... 类别=登录墙/WAF`；`job_status 被改动`（退出码 3，`:180-186,:374-375`）；`Ctrl-C` 退出码 130（`:413-415`）。
- 写锁超时 / 异常时汇总**仍会完整输出**：汇总放在 `finally`（`scripts/backfill_detail.py:552-557`），正常结束、写锁、红线、Ctrl-C 都会走到；异常原因会带出但不会吞掉汇总。这一条是 F5 修复的核心。

## 3. 跑完怎么验（红线）

逐条在主库执行，任一不满足就不要继续下一批（SQL 与期望来自本轮 baidu-REPORT §2）：

| # | 断言 | SQL | 期望 |
|---|---|---|---|
| A1 | 完整正文不能倒退 | `select count(*) from job where length(job_desc) > 200` | ≥ 16 |
| A2 | 状态分布一字不改 | `select job_status, count(*) from job group by 1 order by 1` | ACTIVE 42 / NEW 513 |
| A3 | 薪资不被碰 | `select count(*) from job where min_salary = 0 or max_salary = 0` | = 10 |
| A4 | 行数不增不删 | `select count(*) from job` | = 555 |
| A5 | 回填行 `job_key` 非空 | `select count(*) from job where job_key is null or job_key = ''` | = 0 |

观察项（不是红线，跑完要报）：`select count(*) from job where length(job_desc) <= 200` 应显著小于 539；`detail_html` 非空条数仍为 0 属已知缺口（登录态打不开详情页），如实报、不要藏（baidu-REPORT §2 补充项；`backfill_detail.py:363-372`）。

## 4. 失败回滚

- **备份位置**：与主库同目录，`<库名>.bak-<时间戳>`（`backfill_detail.py:130-134`）；`--dry-run` 不产生备份（`:246-248`）。
- **校验备份**：脚本已在备份后自动执行 `PRAGMA integrity_check`，不通过即中止并删除半截文件；手动核对时仍可自己打开备份执行同一命令，返回 `ok` 才算可用。备份生命周期以 `scripts/backfill_detail.py:183-215` 的注释为唯一规则。
- **可以 `--resume`**：上一批正常结束或 `Ctrl-C` 中断；状态文件 `ok` 列表里的岗位已提交，`--resume` 跳过它们，失败项排到最后、有余额才重试（`backfill_detail.py:219,:226-234`）。
- **必须人工介入**：退出码 3（`job_status` 被改动，写入期间已回滚该条）；备份 `integrity_check` 不通过；状态文件损坏、半截、或记录的库与当前不符（`StateFileError` 拒绝启动且一字不动）；写锁等满预算后明确失败——但汇总仍会打印，不再出现"半截状态文件 + 无汇总"的组合。
- **回滚动作**：停脚本 → 确认有一份 `integrity_check=ok` 的备份 → 用该备份覆盖主库，再按状态文件对账后决定是否 `--resume`。
