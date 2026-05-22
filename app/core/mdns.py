from __future__ import annotations

import socket
import logging

logger = logging.getLogger("wade.mdns")

_zc  = None
_svc = None
_cached_lan_ip: str = "127.0.0.1"

def get_cached_lan_ip() -> str:
    """Return the LAN IP that was detected when start_mdns() last ran."""
    return _cached_lan_ip

def get_lan_ip() -> str:
    """Return the machine's primary LAN IP by probing a route (no traffic sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

def start_mdns(port: int) -> str:
    """Advertise wade.local via mDNS. Returns the detected LAN IP regardless of whether zeroconf is available."""
    global _zc, _svc, _cached_lan_ip
    lan_ip = get_lan_ip()
    _cached_lan_ip = lan_ip

    try:
        from zeroconf import Zeroconf, ServiceInfo  # type: ignore

        _zc = Zeroconf()
        _svc = ServiceInfo(
            "_http._tcp.local.",
            "wade._http._tcp.local.",
            addresses=[socket.inet_aton(lan_ip)],
            port=port,
            properties={"path": "/ui"},
            server="wade.local.",
        )
        _zc.register_service(_svc)
        logger.info("[mDNS] Advertising wade.local → %s:%d", lan_ip, port)
    except ImportError:
        logger.warning("[mDNS] zeroconf not installed — wade.local unavailable. Run: pip install zeroconf")
    except Exception as e:
        logger.warning("[mDNS] Could not start mDNS: %s", e)

    return lan_ip

def stop_mdns() -> None:
    """Unregister the mDNS service and close the Zeroconf instance."""
    global _zc, _svc
    if _zc and _svc:
        try:
            _zc.unregister_service(_svc)
            _zc.close()
            logger.info("[mDNS] Stopped.")
        except Exception:
            pass
    _zc = None
    _svc = None