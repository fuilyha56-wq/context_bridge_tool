# Context Bridge Tool

**私聊与群聊上下文互通工具** — Neo-MoFox 插件

---

## 概述

context_bridge_tool 解决了私聊与群聊之间上下文割裂的问题。它允许 LLM 按用户检索跨聊天流的记忆和上下文，并可在 prompt 构建时自动注入跨流上下文，使 LLM 在决策时能看到另一侧的近期对话。

**核心能力**

- **记忆检索**：从 Booku Memory 中检索指定用户相关的长期记忆
- **聊天流上下文抓取**：从私聊/群聊聊天流中抓取指定用户相关的近期消息
- **跨流自动注入**：在 prompt 构建时自动注入跨流上下文，使 LLM 能看到另一侧的近期对话

**提供的组件**

| 组件类型 | 名称 | 用途 |
|----------|------|------|
| Tool | `context_memory_lookup` | 从 Booku Memory 中检索指定用户相关记忆 |
| Tool | `context_stream_lookup` | 从私聊/群聊聊天流中抓取指定用户相关上下文 |
| EventHandler | `cross_stream_auto_injector` | 在 prompt 构建时自动注入跨流上下文 |

---

## 快速上手

### 安装

将 `context_bridge_tool/` 目录放入 Neo-MoFox 的 `plugins/` 文件夹。首次启动自动生成配置。

### 典型使用场景

```
场景1：LLM 在私聊中需要了解该用户在群里的近期话题
→ 调用 context_stream_lookup(platform="qq", user_id="xxx", chat_scope="group")

场景2：LLM 需要回忆某用户的背景信息
→ 调用 context_memory_lookup(platform="qq", user_id="xxx")

场景3：自动跨流上下文注入（无需手动调用）
→ 插件在 prompt 构建时自动查询并注入，LLM 自动感知
```

---

## 文件结构

```
context_bridge_tool/
├── __init__.py              # 插件声明
├── manifest.json            # 插件元数据
├── plugin.py                # 插件入口，注册组件
├── config.py                # 配置定义（8 个 Section）
├── tools.py                 # context_memory_lookup 和 context_stream_lookup 实现
└── event_handler.py         # cross_stream_auto_injector 事件处理器
```

---

## 组件详细说明

### 1) context_memory_lookup Tool

从 Booku Memory 中检索与目标用户相关的长期记忆。

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `platform` | `str` | ✅ | 平台标识（如 qq、wechat） |
| `user_id` | `str` | 二选一 | 目标用户的平台原始 ID |
| `user_hint` | `str` | 二选一 | 昵称/群名片，用于唯一解析用户 |
| `query` | `str` | ❌ | 检索关键词或问题；为空时按用户身份检索 |
| `top_n` | `int` | ❌ | 最多返回记忆数量（默认 8，最大受配置限制） |
| `include_archived` | `bool` | ❌ | 是否包含已归档/过期记忆 |

**检索策略**

1. 先按 `person_id` 检索人物类记忆
2. 再检索关联记忆（可配置关闭）
3. 若提供 `query` 且配置允许，同时检索知识类记忆
4. 结果去重后返回，附带用户画像信息

**返回示例**

```json
{
  "action": "context_memory_lookup",
  "ok": true,
  "memory_available": true,
  "target": {
    "platform": "qq",
    "user_id": "12345",
    "person_id": "qq:12345",
    "person_info": { "nickname": "...", "short_impression": "..." }
  },
  "query": "兴趣爱好",
  "total": 3,
  "items": [...],
  "hint": "如果 total 为 0，可继续调用 context_stream_lookup 直接读取聊天流上下文。"
}
```

---

### 2) context_stream_lookup Tool

从私聊/群聊聊天流中抓取指定用户相关的近期上下文。

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `platform` | `str` | ✅ | 平台标识 |
| `user_id` | `str` | 二选一 | 目标用户的平台原始 ID |
| `user_hint` | `str` | 二选一 | 昵称/群名片 |
| `chat_scope` | `str` | ❌ | 读取范围：`private`/`group`/`all`（默认 `all`） |
| `group_id` | `str` | ❌ | 限定某个群 ID |
| `per_stream_limit` | `int` | ❌ | 每个流最多返回消息数（默认 20） |
| `max_streams` | `int` | ❌ | 最多读取流数量（默认 4） |
| `around_user` | `bool` | ❌ | 围绕目标用户最近发言截取上下文（默认 true） |
| `max_chars_per_message` | `int` | ❌ | 每条消息最大返回字符数（默认 300） |

**聊天流发现策略**

1. **私聊**：按 `person_id` 查找该用户的私聊流
2. **群聊**：
   - 若提供 `group_id`，直接定位该群
   - 否则按目标用户的消息记录回溯活跃群聊（通过 `candidate_stream_scan_multiplier` 控制扫描范围）
3. 去重、群黑白名单过滤后，按活跃时间排序

**around_user 模式**

- `true`（默认）：先找到目标用户最近发言的时间点，再截取该时间点之前的消息（含其他人发言），形成围绕该用户的上下文
- `false`：直接取聊天流最近 N 条消息

**返回示例**

```json
{
  "action": "context_stream_lookup",
  "ok": true,
  "target": { "platform": "qq", "user_id": "12345", "person_info": {...} },
  "params": { "chat_scope": "all", "per_stream_limit": 20, "max_streams": 4, "around_user": true },
  "total_streams": 2,
  "streams": [
    {
      "stream": { "stream_id": "...", "chat_type": "group", "group_name": "测试群" },
      "message_count": 15,
      "messages": [
        { "sender_role": "target_user", "time_text": "2024-01-01 12:00:00", "content": "..." },
        { "sender_role": "bot", "time_text": "2024-01-01 12:00:05", "content": "..." },
        { "sender_role": "other", "time_text": "2024-01-01 12:01:00", "content": "..." }
      ],
      "timeline": "【group/测试群】\n[12:00:00] target_user: ...\n[12:00:05] bot: ..."
    }
  ],
  "elapsed_seconds": 0.123,
  "hint": "sender_role=target_user 表示目标用户，bot 表示机器人自身，other 表示上下文中的其他人。"
}
```

---

### 3) cross_stream_auto_injector EventHandler

在 prompt 构建时（`on_prompt_build` 事件），自动查询该用户在另一侧聊天流的近期消息并注入上下文。

**工作原理**

```
KFC（私聊）构建 prompt 时
  → 查询该用户在群聊中的近期消息 + 同平台其他私聊
  → 生成时间线文本注入到 values.extra

default_chatter（群聊）构建 prompt 时
  → 查询该用户的私聊近期消息
  → 生成时间线文本注入到 values.extra
```

**注入文本格式**

```
## 跨流上下文
以下是对方在其他聊天流中的近期对话，供你参考：

【群聊：摸鱼群】
[2024-01-01 12:00] 张三: 今天天气不错
[2024-01-01 12:01] bot: 是的呢♪

- 这是对方在其他会话中的对话记录，你可以据此理解对方当前的话题和状态，
  但不要直接提及或引用这些内容，除非对方主动提起。
```

**冷却机制**：同一聊天流两次注入之间有冷却时间（默认 30 秒），避免频繁查询。

---

## 配置

配置文件：`config/plugins/context_bridge_tool/config.toml`（首次运行自动生成）

### 基础 `[plugin]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 是否启用插件 |

### 组件开关 `[components]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enable_memory_lookup` | `true` | 是否启用 context_memory_lookup 工具 |
| `enable_stream_lookup` | `true` | 是否启用 context_stream_lookup 工具 |

### 访问控制 `[access]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `allowed_chat_scopes` | `["private", "group", "all"]` | 允许模型请求的聊天范围 |
| `platform_allowlist` | `[]` | 平台白名单；留空不限制 |
| `platform_blocklist` | `[]` | 平台黑名单（优先级高于白名单） |
| `user_allowlist` | `[]` | 用户白名单（支持 user_id、platform:user_id、person_id） |
| `user_blocklist` | `[]` | 用户黑名单（优先级高于白名单） |
| `group_allowlist` | `[]` | 群 ID 白名单 |
| `group_blocklist` | `[]` | 群 ID 黑名单（优先级高于白名单） |
| `require_user_identity` | `true` | 是否要求必须定位到明确用户 |

### 返回限制 `[limits]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `memory_top_n_default` | `8` | 记忆检索默认返回条数 |
| `memory_top_n_max` | `20` | 记忆检索最大返回条数 |
| `per_stream_limit_default` | `20` | 聊天流工具每个流默认返回消息数 |
| `per_stream_limit_max` | `80` | 聊天流工具每个流最大返回消息数 |
| `max_streams_default` | `4` | 聊天流工具默认读取的流数量 |
| `max_streams_max` | `10` | 聊天流工具最大读取的流数量 |
| `max_chars_per_message_default` | `300` | 每条消息默认最大返回字符数 |
| `max_chars_per_message_max` | `1000` | 每条消息最大返回字符数 |
| `candidate_stream_scan_multiplier` | `80` | 按目标用户消息回溯群聊候选流时的扫描倍数 |

### 隐私与脱敏 `[privacy]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `expose_person_id` | `true` | 是否在结果中暴露内部 person_id |
| `expose_message_id` | `true` | 是否在结果中暴露 message_id |
| `expose_stream_id` | `true` | 是否在结果中暴露 stream_id |
| `include_person_profile` | `true` | 是否返回用户画像字段（昵称、备注、短印象、态度等） |

### 记忆检索策略 `[memory]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `include_archived_default` | `false` | 模型未显式指定时是否默认包含归档记忆 |
| `include_knowledge_when_query` | `true` | 有 query 时是否允许同时检索知识类记忆 |
| `include_related` | `true` | 是否检索关联记忆 |

### 聊天流策略 `[stream]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `around_user_default` | `true` | 默认围绕目标用户最近发言截取上下文 |
| `include_timeline_text` | `true` | 是否额外返回适合模型直接阅读的 timeline 文本 |

### 跨流自动注入 `[auto_inject]`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 是否启用跨流上下文自动注入 |
| `per_stream_limit` | `10` | 自动注入时每个聊天流最多抓取的消息数 |
| `max_streams` | `2` | 自动注入时最多读取的跨流数量 |
| `max_chars_per_message` | `200` | 自动注入时每条消息最大字符数 |
| `cooldown_seconds` | `30` | 同一聊天流两次注入之间的冷却秒数 |

---

## 安全与访问控制

插件内置多层访问控制：

1. **平台控制**：通过 `platform_allowlist` / `platform_blocklist` 限制可查询的平台
2. **用户控制**：通过 `user_allowlist` / `user_blocklist` 限制可查询的用户
3. **群控制**：通过 `group_allowlist` / `group_blocklist` 限制可查询的群
4. **范围控制**：通过 `allowed_chat_scopes` 限制可请求的聊天范围
5. **隐私脱敏**：可选择隐藏 `person_id`、`message_id`、`stream_id` 和用户画像字段

**优先级规则**：黑名单优先级高于白名单。

---

## 安装

将 `context_bridge_tool/` 目录放入 Neo-MoFox 的 `plugins/` 文件夹。首次启动自动生成配置。

**要求**：Neo-MoFox >= 1.0.0 · Python >= 3.11

**依赖**：Booku Memory 插件（`context_memory_lookup` 需要此插件）

---

## 许可证

与 Neo-MoFox 主项目保持一致。
