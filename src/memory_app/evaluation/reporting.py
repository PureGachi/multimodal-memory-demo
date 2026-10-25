"""Pure Markdown rendering of completed reports; never calls models."""
import json


def markdown_report(report):
    lines = ["# Mem-Gallery 本地检索诊断", "", "不是官方比赛成绩，不生成答案、不评价答案正确率。", "",
             "本实验索引所选场景的全部历史，不模拟逐会话/时间截止评测；是否处理原图见 metadata.raw_images_used。",
             "K 按消息计数；命中任一标注轮次算 Hit，找回标注轮次的比例算 Recall；MRR 只看前 K 条。",
             "同一轮的 user/assistant 都视为该轮证据，属于较宽松的轮次级评估，不保证能回答问题。",
             "无 clue 的题只展示候选、不计分；不是拒答准确率。报告中的答案只供检查，未写入记忆。", "",
             "```json", json.dumps(report["metadata"], ensure_ascii=False, indent=2), "```", "",
             f"索引耗时：{report['index_seconds']:.2f} 秒；消息数：{report['indexed_messages']}；无 clue 题数：{report['no_clue_questions']}。", "",
             "## 总体结果", "", "| 通道 | 计分题数 | Hit@K | Recall@K | MRR@K | 全部证据找回率 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for name, values in report["summary"].items():
        scores = ["—" if values[k] is None else f"{values[k]:.3f}" for k in
                  ("hit_at_k", "recall_at_k", "mrr_at_k", "all_evidence_found")]
        lines.append(f"| {name} | {values['scored_questions']} | " + " | ".join(scores) + " |")
    for detail in report["details"]:
        lines += ["", f"## Q{detail['number']} [{detail['point']}]", "", detail["question"], "",
                  f"标注轮次：{', '.join(detail['clues']) or '无 clue（不计分）'}", "",
                  "参考答案（仅用于人工检查）：", "", "```text", str(detail["answer"]), "```", "",
                  "### 标注证据原文", ""]
        lines += [f"![提问附图]({image['image']})" for image in detail.get("query_images", [])]
        lines += ["检索实际使用的文字（含临时图片线索，不写入历史）：", "```text", detail.get("retrieval_query", detail["question"]), "```", ""]
        for gold in detail["gold_evidence"]:
            lines += [f"#### {gold['round']}（{gold['date']}）", ""]
            for message in gold["messages"]:
                lines += [message["role"], "", "```text", message["content"], "```", ""]
                lines += [f"![标注证据图片（预处理情况见 metadata）]({image})" for image in message.get("images", [])]
        if detail.get("expected_images"):
            lines += ["### 参考图片", "", "依据：" + detail["expected_image_basis"], ""]
            lines += [f"![{image['image_id']}]({image['image']})" for image in detail["expected_images"]]
        for name, channel in detail["channels"].items():
            lines += [f"### {name} · {channel['latency_ms']:.1f} ms", "",
                      "指标：`" + json.dumps(channel["metrics"], ensure_ascii=False) + "`", ""]
            for item in channel["returned"]:
                mark = "命中标注轮次" if item["round"] in detail["clues"] else "非标注轮次"
                lines += [f"#### 第 {item['rank']} 条 · {item['round']} · {item['role']} · {mark}", "",
                          f"通道分数：{item['score']:.6f}", "", "```text", item["content"], "```", ""]
                lines += [f"![实际召回图片]({image})" for image in item.get("images", [])]
                for artifact in item.get("image_artifacts", []):
                    if artifact.get("description"):
                        lines += ["图片描述（视觉派生）：", "```text", artifact["description"], "```", ""]
                    lines += ["OCR（派生信息）：", "```text", artifact["visible_text"], "```", ""]
                    if artifact.get("contextual_description"):
                        lines += ["对话背景（说话者陈述，不是视觉事实）：", "```text", artifact["contextual_description"], "```", "",
                                  "背景来源消息：" + ", ".join(m["message_id"] for m in artifact.get("context_messages", [])), ""]
    return "\n".join(lines) + "\n"
