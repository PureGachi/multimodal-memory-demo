# 多模态记忆 Demo v0.1

按功能组织的 Python Add/Search 服务。SQLite 保存原始证据与向量，本地目录保存原图；使用 Ollama `nomic-embed-text:latest` 做文本向量检索，加中文字符/双字词与英文词的 BM25 召回，再以 RRF 融合。

当前是 caption-compatible baseline：检索消息里的文本与图片附带说明，返回原始有序图文。图片查询支持完全相同图片的内容哈希匹配；没有图片语义 embedding、OCR 或图片理解。暂不调用 DeepSeek，也无需 API 费用。

## 安装（PowerShell / Conda）

在项目根目录执行：

```powershell
conda create --prefix .\.conda python=3.11 pip -y
conda activate .\.conda
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
```

当前工作区已创建环境时，不必重复创建。未初始化 Conda 的终端可以直接使用 `.\.conda\python.exe`。

`requirements-lock.txt` 记录本次验证环境的依赖版本；更新依赖时可使用 `python -m pip install -e '.[dev]'`，验证后再更新版本记录。

## 启动

先启动已有 Ollama，确保模型标签为 `nomic-embed-text:latest`。默认地址是 `http://localhost:11434`。

```powershell
.\.conda\python.exe -m uvicorn memory_app.main:create_app --factory --host 127.0.0.1 --port 8000
```

交互接口文档：<http://127.0.0.1:8000/docs>。默认只绑定本机，未设置鉴权；停止服务按 Ctrl+C。Health 检查服务，不代表 Ollama 实时可用；默认启动时会检查模型存在。

离线词法模式：

```powershell
$env:MEMORY_EMBEDDINGS = 'none'
.\.conda\python.exe -m uvicorn memory_app.main:create_app --factory --host 127.0.0.1 --port 8000
```

运行配置见 `.env.example`，通过环境变量设置，示例文件不会自动加载。切换 embedding 模型需要重建向量；词法索引仍可用。向量版本包含模型 digest、前缀与分块版本，避免混用不同模型。

## 一次文本写入与查询

```powershell
$body = @{
  request_id = 'demo-001'; user_id = 'alice'; session_id = 'session-1'
  messages = @(@{role = 'user'; content = '我喜欢蓝色机械键盘'})
} | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:8000/add' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$query = @{query = '我喜欢什么键盘？'; user_id = 'alice'; top_k = 3} | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:8000/search' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($query))
```

图文内容使用官方有序数组：

```json
[{"type":"text","text":"3号机床出现红色告警灯"},
 {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]
```

## 验证

```powershell
.\.conda\python.exe -m pytest -q
# 在另一个终端、服务运行时执行真实 HTTP smoke（也调用本机 Ollama）
.\.conda\python.exe scripts\smoke.py
# 写入三条记忆，展示三个问题找回的原始证据
.\.conda\python.exe scripts\demo.py
# 独立小评测：真实 Ollama、词法/向量/融合对比，使用临时数据
.\.conda\python.exe -X utf8 scripts\evaluate.py
```

测试覆盖持久化重启、用户隔离、同请求重试/冲突、图文顺序与原图恢复、非法输入、top_k、鉴权、并发重试，以及向量故障不提交和查询降级。测试采用受控向量提供者；HTTP smoke 验证实际启动配置。Smoke 使用独立用户并留下少量演示数据。

2026-10-02 扩展检查与修复后共 29 项通过，增加数据库回滚重试、丢图恢复、JPEG/WebP、Ollama 异常返回、长消息尾部与模型版本隔离。原图缺失时 Search 返回明确 503，相同 Add 重试可补回缺失原图。详细发现与真实检索小评测见[学习与修复笔记](笔记/2026-10-02-09-Demo学习思路与测试修复结果.md)。

## 文件职责

详细学习文档与调用图见[2026-10-02 V0 源码导读](笔记/2026-10-02-V0源码导读/2026-10-02-00-阅读导航.md)，共导航页与七个章节，按实际实现定位源码。

`main.py` 负责装配；`api/` 负责 HTTP；`memory/service.py` 对外提供业务入口，`add.py`、`search.py` 协调流程。`storage/` 保存证据及幂等状态，`media/` 保存原图，`embeddings/` 生成向量，`vector_stores/` 查询向量，`retrieval/` 做 BM25 与 RRF。没有当前需求的视觉/LLM模块尚未创建。

详见[架构计划](笔记/2026-10-02-06-多模态记忆系统完整架构计划.md)。本实现独立编写，功能组织参考 [Mem0](https://github.com/mem0ai/mem0)，未复制其源码。接口参考 [AML 规范](https://agentmemories.ai/rules)，embedding 调用参考 [Ollama 文档](https://docs.ollama.com/api/embed)。

## 已知边界与后续

- 检索在单用户数据中线性扫描，适用于小型本地 Demo，尚未验证 Full 评测容量或中文准确率。向量检索返回相似候选，不等同于可靠的无证据判定。
- Add 默认要求文本向量生成成功才提交，Search 模型故障时降级词法与图片哈希召回。每条消息一个单元；文本按 400 字符分块建立向量，返回完整原消息。
- 单图 10 MiB，Add/返回图像合计 30 MiB；top_k 支持 0–100。暂未提供整体 HTTP 请求体限额或速率限制，服务限本地使用。
- 原始数据与向量同 SQLite 事务提交；图片在提交前按用户范围的哈希路径原子保存。数据库提交失败可能留下未引用图片，需要后续清理机制，不会形成可查询的半成品。并发重复请求可能重复计算向量，但数据库不会重复写入。
- 配置与凭据不进入 Git。尚未实现按用户删除、TTL、Docker、公网部署或官方 smoke；正式提交前补齐，并核对当期模型限制。数据目录仅用于当前本地 demo。
