# 正式评测数据保留与删除（2026-10-25）

## 规则和当前状态

按 [AML 官方数据处理责任](https://agentmemories.ai/rules)（2026-10-25 核对），评测数据及派生副本仅用于当前任务，限制访问，避免多余正文日志，在**任务完成后 30 天内**删除。延长保留须事先获得官方书面同意。

这里的起点是实际任务完成时间，不是今天、材料提交日或比赛统一截止日。当前没有正式任务完成时间，所以不虚构截止日。每个任务/运行分别登记；任务中断或取消后的完成时间存在疑问时，先向官方确认。

本地公开 Mem-Gallery 实验和正式评测分开管理：工具不会处理 `data/benchmarks`、`data/evaluations`、`.env`、源码或笔记。公开数据也不能据此获得额外传播许可。

已实现：独立目录登记、截止日期计算、只读预览、到期检查、显式确认清理、最小删除回执和离线安全测试。
未实现或未发生：自动获知官方任务完成、系统定时任务安装、正式数据实际删除、外部服务副本删除或安全擦除。不能把有工具写成已经履行了未来的删除义务。

## 隔离目录

每次正式任务使用 `official/<run-id>/`，不要混用不同任务的数据库：

```text
data/official/evaluation/       # 本机示例；容器内为 /data/official/evaluation/
├── .memory-retention.json     # 登记：真实完成时间与截止日，初始为空
├── runtime/                  # MEMORY_DATA_DIR；SQLite、WAL、图片、派生缓存
├── backups/                  # 本任务备份，如必须保留
├── reports/                  # 私有诊断，如必须产生
└── .memory-deletion-receipt.json  # 清理后最小回执，不保留数据正文/文件名清单
```

外部磁盘、云快照、对象存储、终端转录、Docker 日志和模型供应商留存并不会因为清理这个目录而消失。维护私有外部副本清单，记录位置、负责人、到期处理和完成证据，不包含数据正文或密钥。正式数据只交给官方允许的必要第三方；确认中转/模型供应商的数据政策，工具无法代替供应商删除。

## 本机操作

所有命令从项目根目录执行，不读取 `.env`，不调用模型。

1. 在第一条正式 Add 前登记空目录（已有非空目录拒绝接管）：

   ```powershell
   .\.conda\python.exe -X utf8 scripts/retention.py init --run-id evaluation
   ```

   正式服务的数据路径另外配置为 `data/official/evaluation/runtime`。不会自动更改你现有 `.env`。

2. 任务完成后填写真实 ISO 时间，必须带时区。下面字符串是占位符，须替换，不能直接作为完成时间：

   ```powershell
   .\.conda\python.exe -X utf8 scripts/retention.py complete --run-id evaluation --at '替换为真实完成时间并带时区'
   ```

   截止日为该时间加 30 天；完成时间只允许登记一次，不能自行推迟。若官方批准例外，人工保留批准依据并单独审查，不靠修改脚本绕过。

3. 查看状态/预览，不删除文件：

   ```powershell
   .\.conda\python.exe -X utf8 scripts/retention.py plan --run-id evaluation
   .\.conda\python.exe -X utf8 scripts/retention.py check --run-id evaluation
   ```

   `check`：0 表示尚未到期且已登记完成时间，或数据已按登记清理；2 表示未登记完成时间、已到期或清理后又出现文件，需操作人处理；1 表示检查失败。它不是后台定时器。目标服务器应每天执行并告警，最迟不晚于截止日处理，建议任务完成后尽早清理，而不是等第 30 天。

4. 确认任务已结束，停服务和所有写入者；逐项清理/核查外部副本；重新运行 `plan`，核对路径、文件数和总字节数。预览含私有文件清单，不贴到公开仓库或聊天。

5. 只有明确同意不可恢复删除后，复制刚生成的完整令牌执行：

   ```powershell
   .\.conda\python.exe -X utf8 scripts/retention.py apply --run-id evaluation --confirm '替换为刚预览的完整令牌' --service-stopped --external-copies-reviewed
   ```

   只删除登记目录中的数据文件和空子目录，保留无正文的登记与回执；不移到回收站，不能保证恢复。文件变化使令牌失效；拒绝符号链接、Windows junction、越界和硬链接。执行中断可能仅完成部分删除，应重新检查，不宣称事务性全删。

这只是逻辑删除，不保证 SSD/云盘底层安全擦除，也不能对恶意并发改路径提供操作系统沙箱级保证。必须停服、限制目录写权限；卷快照、备份及供应商副本由操作人另行处理并核对期限。

## Docker 上的对应操作

默认 Compose 已把运行数据放在 `/data/official/evaluation/runtime`，卷仍为 `memory-data`。先 build，再在第一次启动前初始化：

```sh
docker compose -f deploy/compose.yaml run --rm --no-deps memory python -m memory_app.governance.retention init --base /data/official --run-id evaluation
docker compose -f deploy/compose.yaml up -d
```

任务完成后先停止服务，再使用相同 `run --rm --no-deps ...` 运行 `complete`、`plan`、`check` 或显式 `apply`。容器里无需本地 scripts。切勿对已有非空卷补跑 init 或手动伪造登记；先盘点和迁移，不覆盖历史数据。这里没有自动运行 Docker 或删卷。

## 操作人登记清单

- [ ] 正式任务 ID、独立目录与访问负责人已确定。
- [ ] 模型供应商、云日志、快照和所有备份位置已盘点；规则允许其参与。
- [ ] 真实任务完成时间已登记，截止时间与负责人已记录。
- [ ] 每日检查/提醒已在目标环境安装并测试，而不只是写了文档。
- [ ] 截止前停止写入并处理本地文件及全部外部副本。
- [ ] 检查残留、保存最小回执；不把私有清单或正式数据公开。
