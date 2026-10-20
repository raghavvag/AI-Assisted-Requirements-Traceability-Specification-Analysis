Clients keep generating their own refund identifiers and getting them wrong. Let the server own the reference.

## Acceptance criteria
- [ ] A refund request needs only order_id, refund_amount and reason
- [ ] The server generates a refund_reference and returns it in the response
