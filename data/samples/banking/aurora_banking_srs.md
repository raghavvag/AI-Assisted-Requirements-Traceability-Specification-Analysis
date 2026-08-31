# Aurora Digital Banking Platform — Software Requirements Specification

**Version:** 1.0
**Domain:** Retail Digital Banking
**Classification:** Functional Requirements
**Status:** Approved Baseline

---

## Overview

This document specifies the functional requirements for the Aurora Digital Banking Platform API. Every API contract, mobile client, and partner integration must demonstrably satisfy each numbered requirement below. Any modification to the API specification that reduces compliance with these requirements constitutes a specification drift event and must trigger a formal review before deployment.

---

BNK-REQ-01: Customer Authentication and Device Binding
The system must authenticate retail banking customers using a customer identifier and a passcode validated against a salted hash, and must issue a signed JWT access token with a configurable expiry. Every successful authentication response must return a unique session_id and a device_binding_id that ties the session to a registered device fingerprint, enabling session revocation, device de-registration, and a complete audit trail of access events.

BNK-REQ-02: Account Balance and Statement Retrieval
The system must expose authenticated endpoints to list all accounts owned by the customer and to retrieve a paginated transaction statement for a single account. Each account record must include account_id, account_type, currency, available_balance, and ledger_balance. Statement retrieval must accept from_date, to_date, page, and page_size parameters, and each transaction line must carry transaction_id, posting_date, description, amount, and running_balance.

BNK-REQ-03: Beneficiary Management
The system must allow authenticated customers to list, register, and delete payment beneficiaries. Registering a beneficiary requires beneficiary_name, account_number, and ifsc_code, and the response must return a server-assigned beneficiary_id together with an activation_status field. Newly added beneficiaries must remain in a cooling_period state and must not be usable as a transfer destination until the cooling period has elapsed.

BNK-REQ-04: Fund Transfer Idempotency
Every fund transfer request must carry a unique, client-provided idempotency_key in the request body so that a retried or duplicated submission is processed exactly once. The idempotency_key must be echoed back in the transfer confirmation response alongside the transfer_id, giving the client end-to-end proof that a retry did not create a second debit. A transfer request submitted without an idempotency_key must be rejected with a structured validation error.

BNK-REQ-05: Standing Instructions and Scheduled Payments
The system must allow customers to create recurring standing instructions specifying a source account, a registered beneficiary, an amount, a frequency (daily, weekly, monthly), a start_date, and an optional end_date. The response must return an instruction_id and the computed next_execution_date. Standing instructions must be cancellable by the customer at any time before the next execution.

BNK-REQ-06: Debit Card Controls
Authenticated customers must be able to modify the security controls on any card they own through a dedicated endpoint. Controllable attributes must include a freeze flag to instantly block the card, per_transaction_limit, daily_limit, and boolean toggles for international_usage, online_usage, and contactless_usage. The response must confirm the resulting control state for every attribute.

BNK-REQ-07: Step-Up Authentication for High-Value Transfers
All high-value operations — defined as a transfer exceeding 50,000 in account currency, or the first payment to a newly registered beneficiary — must require step-up authentication before the operation is permitted to proceed. The system must expose a dedicated step-up challenge endpoint that issues a time-limited one-time passcode to the customer's registered channel, and a step-up verify endpoint that validates the submitted code and returns a short-lived step_up_token before the primary operation is authorised.

BNK-REQ-08: Transaction Dispute and Chargeback Filing
The system must allow customers to raise a dispute against a settled transaction and to track its resolution. Filing a dispute requires the transaction_id, a dispute_reason drawn from a controlled vocabulary (fraudulent, duplicate_charge, service_not_received, incorrect_amount), and an optional free-text narrative. The system must return a dispute_id, a provisional_credit_status, and an expected_resolution_date, and must expose a retrieval endpoint returning the current dispute status and its full event timeline.
