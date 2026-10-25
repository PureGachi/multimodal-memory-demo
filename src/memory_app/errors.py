"""Safe error feedback shared by CLI, evaluation state and HTTP boundaries.

Never serialize str(exception), request/response bodies, credentials or causes.
retryable describes a possible manual recovery, not authorization to retry.
"""
from memory_app.memory.errors import CheckpointError, Conflict, InvalidContent, ModelUnavailable, StorageUnavailable
import sqlite3
from memory_app.diagnostics import HINTS, classify_failure


MESSAGES = {
    "timeout": ("模型请求超时", "检查网络与服务商状态；超时不代表服务商没有计费，确认后手动续跑。"),
    "network_error": ("连接模型服务失败", "检查网络、TLS、代理或防火墙；不要关闭证书验证。"),
    "http_error": ("模型服务返回 HTTP 错误", "根据状态与服务商代码检查地址、权限、地域、额度和限速。"),
    "invalid_response": ("模型响应格式或向量校验失败", "检查返回数量、index、维度、有限数值和非零向量；不会保存不合规结果。"),
    "invalid_json": ("图片描述不是合法 JSON", "核对服务商是否支持并遵守 response_format。"),
    "invalid_schema": ("图片描述字段不符合要求", "需要 description 和 visible_text 两个字符串字段及非空证据。"),
    "refused": ("视觉模型拒绝处理图片", "检查图片内容与服务商支持范围，不会把拒答保存为证据。"),
    "model_unavailable": ("模型调用失败", "查看模型配置与服务商状态，已完成的检查点会保留。"),
    "checkpoint_error": ("检查点不匹配、损坏或被其他进程占用", "使用原数据、原配置和原参数续跑；不要修改检查点，必要时新建一次实验。"),
    "storage_error": ("本地存储失败", "检查磁盘空间、目录权限与数据库状态。"),
    "config_error": ("配置或输入不符合要求", "检查模式、模型、数据路径和命令参数；不会自动切换模型。"),
    "conflict": ("写入请求冲突", "相同 request_id 必须使用相同内容。"),
    "interrupted": ("任务被中断", "已完成进度保留，可使用同一检查点手动续跑。"),
    "internal_error": ("任务遇到未分类异常", "保留检查点，先排查代码；不自动重跑付费任务。"),
}
PROVIDER_CODES = {"invalid_api_key", "insufficient_quota", "rate_limit_exceeded", "model_not_found",
    "InvalidApiKey", "Throttling", "Arrearage", "AccessDenied", "InvalidParameter", "invalid_parameter_error",
    "credit_balance_exhausted", "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded", "slow_down", "unclassified"}


def error_feedback(exc):
    code, component, retryable, status, provider = "internal_error", "evaluation", False, None, None
    if isinstance(exc, ModelUnavailable):
        code = exc.code if exc.code in MESSAGES else "model_unavailable"
        component = exc.component if exc.component in {"embedding", "vision", "model"} else "model"
        retryable = exc.retryable is True
        status = exc.http_status if type(exc.http_status) is int and 100 <= exc.http_status <= 599 else None
        provider = exc.provider_code if exc.provider_code in PROVIDER_CODES else None
    elif isinstance(exc, CheckpointError):
        code, component = "checkpoint_error", "checkpoint"
    elif isinstance(exc, (InvalidContent, ValueError, FileNotFoundError)):
        code, component = "config_error", "configuration"
    elif isinstance(exc, (StorageUnavailable, OSError, sqlite3.Error)):
        code, component = "storage_error", "storage"
    elif isinstance(exc, Conflict):
        code = "conflict"
    elif isinstance(exc, KeyboardInterrupt):
        code = "interrupted"
    message, hint = MESSAGES[code]
    result = {"code": code, "component": component, "retryable": retryable,
            "http_status": status, "provider_code": provider, "message": message, "hint": hint}
    if code in {"network_error", "timeout", "storage_error"}:
        reason = getattr(exc, "diagnostic", None) if isinstance(exc, ModelUnavailable) else classify_failure(exc)
        reason = reason if isinstance(reason, str) and reason in HINTS else "unknown"
        result["diagnostic"] = reason
        result["hint"] = HINTS[reason] + " 不会自动重试模型请求。"
    return result


def cli_entrypoint(action):
    """Concise, non-secret CLI failures; argparse SystemExit remains unchanged."""
    import json
    import sys
    try:
        action()
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"error": error_feedback(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1) from None
