Removes the 2FA step-up gate from checkout: `two_fa_token` and `x-requires-2fa` are dropped and every order
is placed directly, whatever its value.

Closes #3

**Expected bot verdict:** BLOCK on REQ-09 (Two-Factor Authentication Enforcement).
