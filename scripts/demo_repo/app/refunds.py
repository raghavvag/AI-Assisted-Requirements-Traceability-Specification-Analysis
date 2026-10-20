"""Refund handling. Implements REQ-04 (Refund Transaction Integrity)."""

from fastapi import APIRouter, HTTPException

from app import ledger
from app.models import RefundReceipt, RefundRequest

router = APIRouter(prefix="/api/v1")


@router.post("/refund", response_model=RefundReceipt)
def process_refund(req: RefundRequest) -> RefundReceipt:
    """Refund a completed order idempotently.

    The client-provided transaction_id makes the call idempotent: a repeated
    request returns the original receipt instead of refunding twice, and the id
    is echoed in the receipt for the financial audit trail.
    """
    existing = ledger.find_refund_by_transaction(req.transaction_id)
    if existing is not None:
        return existing
    order = ledger.get_order(req.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if req.refund_amount > order.total:
        raise HTTPException(status_code=422, detail="Refund exceeds the order total")
    refund_id = ledger.record_refund(req.order_id, req.transaction_id, req.refund_amount, req.reason)
    return RefundReceipt(refund_id=refund_id, transaction_id=req.transaction_id, order_id=req.order_id,
                         refund_amount=req.refund_amount, status="processed")
