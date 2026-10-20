Refund requests no longer carry a client-provided `transaction_id`. The server now generates a
`refund_reference` and returns it in the receipt.

Closes #2

**Expected bot verdict:** BLOCK on REQ-04 (Refund Transaction Integrity requires the client transaction_id).
