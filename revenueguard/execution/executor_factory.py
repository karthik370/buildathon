"""
Executor factory: reads EXECUTOR_MODE env var and returns the right executor.

EXECUTOR_MODE=simulated      (default) → SimulatedExecutor
EXECUTOR_MODE=razorpay_test            → RazorpayTestModeExecutor

Both implement ActionExecutor from executor_interface.py.
No changes to detection, diagnosis, decision, or gating logic are needed
when switching modes — the interface contract is identical.

Usage in execution_engine.py:
    from .executor_factory import get_executor
    executor = get_executor()
    result = executor.execute(entity_id, action_type, root_cause, amount, attempt)
"""

from __future__ import annotations

import os
from .executor_interface import ActionExecutor


def get_executor() -> ActionExecutor:
    """
    Return the correct executor based on EXECUTOR_MODE env var.

    Defaults to 'simulated' if not set.
    """
    mode = os.environ.get("EXECUTOR_MODE", "simulated").strip().lower()

    if mode == "razorpay_test":
        from .razorpay_test_executor import RazorpayTestModeExecutor
        return RazorpayTestModeExecutor()
    elif mode == "simulated":
        from .executors import SimulatedExecutor
        return SimulatedExecutor()
    else:
        raise ValueError(
            f"Unknown EXECUTOR_MODE='{mode}'. "
            f"Valid options: 'simulated', 'razorpay_test'."
        )
