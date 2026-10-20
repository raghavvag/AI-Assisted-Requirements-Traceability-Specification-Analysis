"""
Code-level demo regressions (M5), applied to the demo service by
scripts/setup_demo_repo.py. openapi.yaml is never touched: the bot must catch
them from the Python code alone.

  refund   refunds stop taking the client transaction_id     -> BLOCK on REQ-04
           (branch code_drop_transaction_id)
  2fa      the 2FA step-up gate is removed from checkout and
           the step-up policy deleted                         -> BLOCK on REQ-09
           (branch code_drop_2fa_step_up)
"""

# (file, old text, new text), applied in order with str.replace(old, new, 1)
EDITS = [
    ("app/models.py",
     '    transaction_id: str = Field(..., description="Unique client-provided id for idempotent processing and audit")\n',
     ""),
    ("app/models.py",
     '    transaction_id: str = Field(..., description="Echo of the client transaction_id for the audit trail")\n',
     '    refund_reference: str = Field(..., description="Server-generated reference for internal tracking")\n'),
    ("app/refunds.py",
     '''    """Refund a completed order idempotently.

    The client-provided transaction_id makes the call idempotent: a repeated
    request returns the original receipt instead of refunding twice, and the id
    is echoed in the receipt for the financial audit trail.
    """
    existing = ledger.find_refund_by_transaction(req.transaction_id)
    if existing is not None:
        return existing
    order''',
     '''    """Refund a completed order. The server generates the refund reference."""
    order'''),
    ("app/refunds.py",
     "    refund_id = ledger.record_refund(req.order_id, req.transaction_id, req.refund_amount, req.reason)\n"
     "    return RefundReceipt(refund_id=refund_id, transaction_id=req.transaction_id, order_id=req.order_id,\n",
     "    refund_id, reference = ledger.record_refund(req.order_id, req.refund_amount, req.reason)\n"
     "    return RefundReceipt(refund_id=refund_id, refund_reference=reference, order_id=req.order_id,\n"),
]

STEP_UP_EDITS = [
    ("app/twofa.py",
     'def requires_step_up(operation: str, amount: float) -> bool:\n'
     '    """Two-factor authentication (2FA) is required for orders over $500 and for adding a new payment method."""\n'
     '    return operation == "add_payment_method" or amount > HIGH_VALUE_LIMIT\n\n\n',
     ""),
    ("app/twofa.py",
     "    if not requires_step_up(req.operation, req.amount):\n"
     '        raise HTTPException(status_code=400, detail="This operation does not need two-factor verification")\n',
     ""),
    ("app/models.py",
     '    two_fa_token: Optional[str] = Field(None, description="2FA token, required for orders over $500")\n',
     ""),
    ("app/checkout.py",
     "    if cart.subtotal > HIGH_VALUE_LIMIT and not twofa.is_verified(user_id, req.two_fa_token):\n"
     '        raise HTTPException(status_code=403, detail="2FA verification required for high-value orders")\n',
     ""),
    ("app/checkout.py",
     '    """Place an order from the user\'s cart.\n\n'
     "    Orders above $500 require a verified two-factor (2FA) step-up token before\n"
     "    they are processed.\n"
     '    """\n',
     '    """Place an order from the user\'s cart in a single request, whatever its value."""\n'),
]

SCENARIOS = {"refund": EDITS, "2fa": STEP_UP_EDITS}


def apply(files: dict, scenario: str = "refund") -> dict:
    """{path: source} -> a copy with the regression applied. Raises if an edit does not match."""
    out = dict(files)
    for path, old, new in SCENARIOS[scenario]:
        if old not in out[path]:
            raise ValueError(f"code regression edit does not match {path}: {old[:60]!r}")
        out[path] = out[path].replace(old, new, 1)
    return out
