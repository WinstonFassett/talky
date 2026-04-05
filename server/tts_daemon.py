#!/usr/bin/env python3
"""TTS daemon — backward-compatible wrapper around voice_daemon.

The voice daemon handles all TTS commands identically to the old TTS-only daemon.
This module re-exports everything for backward compatibility with existing callers.
"""

import os
import sys

# Add project root for shared imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Re-export everything from voice_daemon
from voice_daemon import (  # noqa: F401
    VoiceDaemon as TTSDaemon,
)
from voice_daemon import (
    main,
    send_speak_request,
    start_daemon,
    stop_daemon,
)

if __name__ == "__main__":
    main()
