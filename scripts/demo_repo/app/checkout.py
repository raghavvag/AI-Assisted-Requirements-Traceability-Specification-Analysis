"""Checkout. Implements REQ-05 (order placement) and REQ-09 (2FA for high-value orders)."""

from fastapi import APIRouter, HTTPException

from app import ledger, twofa
from app.models import CheckoutRequest, Order

router = APIRouter(prefix="/api/v1")

HIGH_VALUE_LIMIT = 500.0


@router.post("/checkout", response_model=Order, status_code=201)
def place_order(req: CheckoutRequest, user_id: str) -> Order:
    """Place an order from the user's cart.

    Orders above $500 require a verified two-factor (2FA) step-up token before
    they are processed.
    """
    cart = ledger.get_cart(user_id)
    if cart.subtotal > HIGH_VALUE_LIMIT and not twofa.is_verified(user_id, req.two_fa_token):
        raise HTTPException(status_code=403, detail="2FA verification required for high-value orders")
    order_id = ledger.create_order(user_id, cart, req.shipping_address, req.delivery_method,
                                   req.payment_method_token)
    return Order(order_id=order_id, estimated_delivery_date=ledger.delivery_estimate(req.delivery_method),
                 status="confirmed")
