"""bridge-for-agents — answer Claude Code's prompts from a chat channel.

The daemon itself lives in :mod:`bridge_for_agents.bridge`. It is intentionally
not imported here: that module reads its configuration from the environment at
import time, so importing this package stays free of environment requirements.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
