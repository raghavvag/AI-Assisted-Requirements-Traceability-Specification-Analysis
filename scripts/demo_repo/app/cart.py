"""Shopping cart. Implements REQ-03 (Shopping Cart Management)."""

from fastapi import APIRouter

from app import ledger
from app.models import Cart

router = APIRouter(prefix="/api/v1")


@router.get("/cart", response_model=Cart)
def get_cart(user_id: str) -> Cart:
    """Return the user's persistent cart with every item and the server-computed subtotal."""
    return ledger.get_cart(user_id)


@router.post("/cart/items", response_model=Cart)
def add_cart_item(user_id: str, product_id: str, quantity: int = 1) -> Cart:
    """Add a product to the persistent cart and return the updated cart state."""
    ledger.add_item(user_id, product_id, quantity)
    return ledger.get_cart(user_id)
