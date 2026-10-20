"""Request and response models of the Nexus storefront API."""

from typing import List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., description="Account email")
    password: str = Field(..., description="Plain-text password, checked against the stored hash")


class Session(BaseModel):
    access_token: str = Field(..., description="Signed JWT access token")
    expires_in: int = Field(..., description="Seconds until the token expires")
    session_id: str = Field(..., description="Unique session identifier for revocation and audit")


class CartItem(BaseModel):
    item_id: str
    product_id: str
    quantity: int
    unit_price: float


class Cart(BaseModel):
    cart_id: str
    items: List[CartItem]
    subtotal: float = Field(..., description="Server-computed cart subtotal")


class RefundRequest(BaseModel):
    order_id: str = Field(..., description="The order to refund")
    transaction_id: str = Field(..., description="Unique client-provided id for idempotent processing and audit")
    refund_amount: float = Field(..., description="Amount to refund")
    reason: str = Field(..., description="Reason for the refund")


class RefundReceipt(BaseModel):
    refund_id: str
    transaction_id: str = Field(..., description="Echo of the client transaction_id for the audit trail")
    order_id: str
    refund_amount: float
    status: str


class CheckoutRequest(BaseModel):
    shipping_address: str = Field(..., description="Validated shipping address")
    delivery_method: str = Field(..., description="Selected delivery method")
    payment_method_token: str = Field(..., description="Token of the selected payment method")
    two_fa_token: Optional[str] = Field(None, description="2FA token, required for orders over $500")


class Order(BaseModel):
    order_id: str
    estimated_delivery_date: str
    status: str
