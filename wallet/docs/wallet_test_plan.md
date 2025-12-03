# Wallet manual/integration test plan

This checklist can be used for a short manual run or automated integration test that exercises the critical wallet flows.

## Preconditions
- Build or install a dev/test build of the Animica Wallet mobile app.
- Use a test/dev network endpoint (default from `.env` or the built-in chain registry).
- Start from a clean install with no stored accounts, or ensure the vault is locked.

## Test matrix
1. **Create a new wallet**
   - Launch the app and follow the onboarding flow: **Welcome → Create Wallet → Set PIN/Password → Backup Seed**.
   - Confirm the mnemonic is shown and can be revealed/hidden, then require acknowledgment of the backup.
   - Finish onboarding and land on **Home / Portfolio** with the first account selected.
   - Expected: vault unlock succeeds, address is visible, and a fresh balance fetch occurs.

2. **Restore from seed**
   - From the welcome/onboarding screen choose **Import / Restore Wallet**.
   - Enter a known 12/24-word mnemonic, set a PIN/password, and proceed.
   - Verify the derived account on the **Home** screen matches the expected address (compare against reference).
   - Expected: balances load for the restored account; recent activity appears if available.

3. **View balance**
   - From **Home / Portfolio**, confirm the primary balance and token list populate after network sync.
   - Toggle between networks if supported (e.g., devnet/testnet) and ensure balances refresh per network.
   - Expected: no stale values; loading and error states resolve with accurate numbers.

4. **Send a transaction**
   - Navigate to **Send** from the home action sheet or tab bar.
   - Fill recipient address, amount, optional memo/data, and submit.
   - On the **Review / Confirm Transaction** screen, validate gas/fee preview and approve.
   - After approval, observe the **Activity / Transactions** screen for pending → confirmed status and hash/link to explorer.
   - Expected: transaction hash returned, status updates to confirmed, balance decrements accordingly.

5. **Handle a failed transaction**
   - From **Send**, submit a transaction expected to fail (e.g., insufficient balance or intentionally high gas/low fee scenario on devnet).
   - On **Review**, proceed to approval and wait for execution.
   - Observe failure surfaced in **Activity / Transactions** detail view with an error reason.
   - Expected: clear error message, no balance change, ability to retry or dismiss.

## Notes
- Capture device logs for regressions (Flutter logs via `flutter run` or platform-specific consoles).
- Re-lock and unlock the vault between runs to verify session persistence.
- If feature flags are enabled (dev tools, randomness, DA, etc.), repeat happy-path flows with flags toggled to ensure compatibility.
