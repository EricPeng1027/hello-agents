# 类型结构覆盖配置

每个 YAML 对应一个已注册的材料类型（`<type_id>.yaml`），**只写想覆盖的字段**，
未写的字段用默认定义。删除文件即恢复默认。

```yaml
# 例：work_summary.yaml —— 所有字段可选
name: 年度工作总结              # 连显示名称都能改
system_prompt: "你是一位资深的工作总结撰写专家……"   # 覆盖角色设定
material_role_hint: "参考历史总结的行文风格……"        # 覆盖材料使用说明
word_count_total: 2300                                # 覆盖总目标字数

sections:            # 整体替换章节骨架（非逐项合并，写全量）
  - key: overview    # 章节标识（同一类型内唯一）
    title: 总体概述   # 章节标题
    target_words: 300
    hints: 概述本期工作背景、整体进展与核心定位。
    required: true   # 可选，默认 true
```

## 自定义类型

在 `config/custom_types/<type_id>.yaml` 写**全量定义**即可新增材料类型
（无需改代码），`name` 与 `sections` 必填，其余同上。范式/工具/材料模式
沿用系统默认（plan_solve + recall_material + RAG/本地自动降级），
材料目录自动隔离到 `data/<type_id>/facts|style`。

## 删除内置类型

在 Web「⚙️ 类型配置」页删除内置类型后，其 type_id 会记入
`_deleted.yaml`（本目录），重启后保持隐藏；磁盘材料目录保留。
Web 删除自定义类型则直接删除其 `config/custom_types/<type_id>.yaml`。

可覆盖字段仅限以上纯数据项；范式（paradigm）、工具（tools）、材料模式等
机制字段不开放。保存后立即生效（无需重启），正在撰写的会话不受影响。
