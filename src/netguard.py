# src/netguard.py
"""联网边界：只允许访问白名单地址，其余一律拦截并记录。"""
import socket, logging
from src.logger import log_event

ALLOW_HOST = "api.deepseek.com"
ALLOW_PORT = 443
_orig_connect = socket.socket.connect


def install_allowlist(run_id: str = "default") -> None:
    """只允许连接白名单地址，其他一律拦截并写日志"""
    def guarded(self, address):
        host, port = address[0], address[1]
        ok = (host == ALLOW_HOST or host.endswith("." + ALLOW_HOST)) and port == ALLOW_PORT
        try:
            log_event(run_id, "network", host=host, port=port, allowed=ok,
                      purpose="llm_api" if ok else "blocked")
        except Exception:
            pass                      # 日志失败不能影响主流程
        if not ok:
            logging.error("[NETGUARD] 拦截非白名单连接 %s:%s", host, port)
            raise PermissionError(f"禁止访问白名单外的地址：{host}:{port}")
        return _orig_connect(self, address)
    socket.socket.connect = guarded


def offline_selftest() -> bool:
    """离线模式启动自检：能连上 API 就说明有模块偷偷联网了"""
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect((ALLOW_HOST, ALLOW_PORT))
        raise RuntimeError("离线模式下检测到网络可达，请检查是否有模块绕过缓存直接调用 API")
    except OSError:
        return True
    finally:
        s.close()