"""Network utilities for Talky remote access configuration."""

import socket
from typing import Optional


def detect_external_hostname(config_host: str, external_host: Optional[str] = None) -> str:
    """
    Detect the appropriate hostname for external access.
    
    Args:
        config_host: Host configuration from settings.yaml (e.g., "localhost", "0.0.0.0", or actual hostname)
        external_host: Optional external_host override from settings.yaml
        
    Returns:
        str: The hostname to use for external connections
    """
    # If external_host is explicitly configured, use it
    if external_host:
        return external_host
    
    # If config_host is not localhost, use it directly
    if config_host and config_host != "localhost":
        if config_host == "0.0.0.0":
            # For 0.0.0.0 binding, try to detect actual hostname
            try:
                hostname = socket.gethostname()
                return hostname
            except Exception:
                # Fallback to localhost if hostname detection fails
                return "localhost"
        else:
            # Use the configured hostname directly
            return config_host
    
    # Default to localhost
    return "localhost"


def get_default_gateway_url(config_host: str, external_host: Optional[str] = None, port: int = 18789) -> str:
    """
    Get the default gateway URL for WebSocket connections.
    
    Args:
        config_host: Host configuration from settings.yaml
        external_host: Optional external_host override from settings.yaml
        port: Port number for the gateway
        
    Returns:
        str: WebSocket URL (e.g., "ws://localhost:18789")
    """
    hostname = detect_external_hostname(config_host, external_host)
    return f"ws://{hostname}:{port}"


def get_browser_url(host: str, port: int, ssl_enabled: bool = False) -> str:
    """
    Get the browser URL for accessing the web interface.
    
    Args:
        host: Host for the browser URL
        port: Port number
        ssl_enabled: Whether HTTPS is enabled
        
    Returns:
        str: Browser URL (e.g., "https://localhost:5173")
    """
    protocol = "https" if ssl_enabled else "http"
    return f"{protocol}://{host}:{port}"
