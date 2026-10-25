"""Static classifications only: never expose exception text, URLs or headers."""
import errno
import socket
import ssl


HINTS = {
    "dns_failure": "域名解析失败：检查 DNS 和当前网络。",
    "tls_certificate": "证书验证失败：检查系统时间、证书和代理证书；不要关闭证书验证。",
    "tls_failure": "TLS 握手失败：检查网络拦截、代理和服务商 HTTPS 配置。",
    "connection_reset": "连接被中途断开：可能是网络设备或服务端断开连接；不能据此确定责任方。",
    "connection_refused": "连接被拒绝：检查目标端口、代理是否运行及防火墙。",
    "remote_protocol": "远端未完整返回 HTTP 响应：检查中转服务稳定性；不要连续重跑付费请求。",
    "local_protocol": "本地 HTTP 请求协议错误：检查配置中是否存在换行或非法字符，不要公开密钥。",
    "proxy_failure": "代理连接失败：检查代理地址、端口与认证配置。",
    "connect_timeout": "建立连接超时：检查网络路由和服务商可达性。",
    "read_timeout": "等待响应超时：可能已到达服务端并产生费用，确认后再续跑。",
    "write_timeout": "发送请求超时：检查上传链路；不能保证服务端未接收请求。",
    "pool_timeout": "等待连接资源超时：检查本地并发和连接资源。",
    "timeout": "网络操作超时：确认服务商状态后手动续跑。",
    "connect_failure": "连接建立失败：检查直连路由、TLS、代理和防火墙。",
    "read_failure": "接收数据失败：连接可能被网络或服务端中断。",
    "write_failure": "发送数据失败：检查上传连接是否被中断。",
    "permission_denied": "文件写入被拒绝或文件被占用：检查权限、杀毒软件和同步程序；不要删除检查点。",
    "disk_full": "磁盘空间不足：清理其他文件，保留当前检查点。",
    "path_too_long": "文件路径过长：需要缩短工作目录路径。",
    "database_locked": "数据库被占用：确认没有其他任务在操作同一检查点。",
    "unknown": "底层原因未分类；不输出原始异常，以免泄露密钥或数据。",
}


def classify_failure(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 16:
        seen.add(id(exc))
        chain.append(exc)
        # Only static categories escape this function, so even deliberately
        # suppressed transport contexts can safely identify a hidden TLS cause.
        exc = exc.__cause__ or exc.__context__
    # Prefer a concrete OS/TLS cause over the SDK's generic connection wrapper.
    for item in chain:
        if isinstance(item, socket.gaierror):
            return "dns_failure"
        if isinstance(item, ssl.SSLCertVerificationError):
            return "tls_certificate"
        if isinstance(item, ssl.SSLError):
            return "tls_failure"
        if isinstance(item, ConnectionResetError) or getattr(item, "winerror", None) in (10054, 10053):
            return "connection_reset"
        if isinstance(item, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(item, PermissionError):
            return "permission_denied"
        if isinstance(item, OSError) and item.errno == errno.ENOSPC:
            return "disk_full"
        if isinstance(item, OSError) and (item.errno == errno.ENAMETOOLONG or getattr(item, "winerror", None) == 206):
            return "path_too_long"
        if getattr(item, "sqlite_errorcode", None) in (5, 6):
            return "database_locked"
    names = {"ConnectTimeout": "connect_timeout", "ReadTimeout": "read_timeout",
             "WriteTimeout": "write_timeout", "PoolTimeout": "pool_timeout",
             "ProxyError": "proxy_failure", "RemoteProtocolError": "remote_protocol",
             "LocalProtocolError": "local_protocol", "ConnectError": "connect_failure",
             "ReadError": "read_failure", "WriteError": "write_failure"}
    for item in reversed(chain):
        if type(item).__module__.split(".")[0] in {"httpx", "httpx2", "httpcore", "httpcore2"}:
            if type(item).__name__ in names:
                return names[type(item).__name__]
        if isinstance(item, TimeoutError):
            return "timeout"
    return "unknown"
