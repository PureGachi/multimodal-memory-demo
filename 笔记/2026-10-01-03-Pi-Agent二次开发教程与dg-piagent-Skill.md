# Pi Agent 二次开发教程与 dg-piagent Skill

记录日期：2026-10-01  
来源作者：冬瓜（GitHub：`buchidonggua`）

## 资源入口

- 在线教程：<https://dg-ai-notes.pages.dev/>
- GitHub 仓库：<https://github.com/buchidonggua/dg-ai-notes>
- `dg-piagent` Skill：<https://github.com/buchidonggua/dg-ai-notes/tree/main/skills/dg-piagent>
- Pi 官方网站：<https://pi.dev/>

## 资源定位

该项目是围绕 Pi Agent 的双轨教程：

1. 源码学习：10章拆解 Agent SDK 的核心设计；
2. 实战上手：7章完成一个可以封装成服务的垂直 Agent；
3. Skill 应用：通过 `dg-piagent` 为 AI 助手提供 Pi SDK 的开发参考和场景手册。

它非常适合作为比赛结束后学习 Pi 二次开发的主教材，但不替代 Pi 官方文档和当前源码。教程中的 API、包名和行为需要与实际安装版本再次核对。

## 源码学习路线

教程的10章源码线索为：

1. 为什么 Pi Agent 值得学习；
2. Pi Agent 的三层架构；
3. Agent Loop；
4. 多模型调用；
5. 工具系统；
6. 消息系统；
7. 事件驱动；
8. 上下文工程；
9. 上下文压缩；
10. 会话管理、恢复与分叉。

与当前 Agent Memory Challenge 关系最直接的是第6、8、9、10章；与后续工业 Agent 工程化关系最直接的是第3、5、7章。

## 二次开发实战路线

教程的7章实战线索为：

1. 环境部署；
2. 核心 API 与三层架构；
3. 模型配置和企业内网模型接入；
4. 系统提示词覆盖；
5. 工具定义；
6. 事件监听与个性化逻辑；
7. 将 Agent 封装成服务并准备上线。

教程使用企业数据分析助手 DataAgent 作为垂直案例。学习时应先跟随案例跑通，再将其数据工具替换成记忆检索、CAD API、几何验证和工业知识库工具。

## dg-piagent Skill

仓库中提供 `skills/dg-piagent/`，结构包含：

```text
dg-piagent/
├─ SKILL.md
└─ references/
```

README说明该 Skill 从两个视角组织：

- SDK 功能视角：用于查询 Pi Agent API和机制；
- 场景应用视角：用于按需求选择上下文注入、事件监听、工具设计等实现方案。

作者说明其 API 核对到 `pi-coding-agent v0.83.0`。这是一项重要的版本边界：以后使用 Skill 时，必须先确认当前 Pi 版本，检查已经变更、废弃或新增的 API。

## 与比赛的直接关联

### 消息系统

帮助理解 Turn、模型消息、工具调用结果如何进入 Agent 状态。

### 上下文工程

帮助决定从长期记忆 Search 得到的证据，如何被编排进本轮模型输入。

### 上下文压缩

帮助处理超长会话，但不能替代比赛中的长期记忆写入、更新、冲突和检索。

### 会话管理

帮助保存、恢复和分叉任务轨迹，可用于比赛调试和失败案例复现。

### 事件监听

可用于记录 Add/Search、Embedding、OCR、Rerank、缓存命中和异常恢复等 Trace。

## 与工业/CAD Agent 的迁移方式

先跟随教程完成 DataAgent，再逐步替换工具：

```text
DataAgent工具                工业/CAD Agent工具
------------------------------------------------
SQL查询                  →   工业知识库检索
数据分析                 →   特征识别模型
图表生成                 →   CAD模型/工程报告生成
危险SQL拦截              →   高风险CAD修改审批
数据校验                 →   CAD内核与几何约束校验
业务日志                 →   设计过程Trace与审计
```

最终希望形成：

```text
Pi Agent编排层
├─ 对话与上下文管理
├─ 工具调用
├─ 事件与Trace
├─ 会话恢复
└─ 权限与审批扩展
        ↓
Python专业服务层
├─ 多模态记忆
├─ RAG与重排
├─ CAD特征识别
├─ CAD序列生成
└─ 几何执行与验证
```

## 推荐学习顺序

1. 当前阶段先完成 Agent Memory Challenge，不立即安装或深挖 Pi；
2. 比赛结束后完成实战P01-P07，跑通 DataAgent；
3. 阅读源码M01-M03，掌握三层架构和 Agent Loop；
4. 阅读M05-M10，重点关注工具、消息、事件、上下文和会话；
5. 审查并试用 `dg-piagent` Skill；
6. 用 Python重写一个最小 Agent Loop，确认真正理解；
7. 将竞赛的记忆系统作为工具接入；
8. 最后搭建工业/CAD垂直 Agent。

## 使用 Skill 前的安全与质量检查

目前只记录资源，不自动安装。正式使用前需要：

1. 完整阅读 `SKILL.md`；
2. 检查 `references/` 中是否包含过期 API；
3. 检查是否包含脚本、命令、网络访问或文件写入要求；
4. 将其声明的 Pi 版本与当前安装版本比较；
5. 优先用项目级安装，避免一开始污染全局环境；
6. 先在实验项目中验证，再用于正式工业 Agent；
7. 遇到 Skill 与官方文档冲突时，以当前官方源码和文档为准。

## 当前判断

该资源值得作为后续 Pi Agent 学习主线。它提供的价值不只是教程，而是把“源码理解—二次开发—Skill辅助”连接成了一条完整路线。我们应当把它视为高质量的学习和开发参考，而不是未经验证的永久真相。

