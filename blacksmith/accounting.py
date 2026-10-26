"""Read-only critical accounting for CLI, retained reports and publication metadata."""

from blacksmith.budget import BudgetLedger
from blacksmith.run_clock import used


def snapshot(context):
    ledger = BudgetLedger(context)
    balance = ledger.balance()
    charged_seconds, incomplete_time = used(context)
    limit = context.execution_config().limits.max_run_seconds
    cap = ledger._cap()
    return {"version": 1, "reported_usd": balance.known_cost_usd,
        "charged_usd": balance.charged_usd, "reserved_usd": balance.reserved_usd,
        "remaining_usd": balance.remaining_usd, "cap_usd": float(cap) if cap is not None else None,
        "accounting_incomplete": balance.accounting_incomplete or balance.reserved_usd > 0,
        "overrun_usd": balance.overrun_usd,
        "unacknowledged_overrun": balance.unacknowledged_overrun,
        "execution_seconds": charged_seconds, "execution_limit_seconds": limit,
        "execution_remaining_seconds": max(0.0, limit - charged_seconds),
        "timing_incomplete": incomplete_time}


def cost_line(data):
    if data['accounting_incomplete']:
        text = (f"total cost: reported ${data['reported_usd']:.2f}; "
                f"charged ${data['charged_usd']:.2f}; reserved ${data['reserved_usd']:.2f}; "
                "accounting incomplete")
    else:
        text = f"total cost: ${data['reported_usd']:.2f}"
    if data['overrun_usd']:
        text += f"; reservation overrun ${data['overrun_usd']:.2f}"
    text += (f"; run cap ${data['cap_usd']:.2f}, remaining ${data['remaining_usd']:.2f}"
             if data['cap_usd'] is not None else "; run cap unlimited (explicit)")
    return text


def clock_line(data):
    text = (f"execution time: {data['execution_seconds']:.2f}s / "
            f"{data['execution_limit_seconds']:.2f}s; "
            f"remaining {data['execution_remaining_seconds']:.2f}s")
    if data['timing_incomplete']:
        text += "; timing incomplete, unfinished slice charged conservatively"
    return text


def print_failure(context, *, out):
    """A failed critical policy may prevent totals; never replace the original error."""
    from blacksmith.receipts import ReceiptError
    try:
        data = snapshot(context)
        print(cost_line(data), file=out)
        print(clock_line(data), file=out)
    except (ReceiptError, OSError, ValueError):
        print("accounting unavailable; original critical records retained", file=out)
