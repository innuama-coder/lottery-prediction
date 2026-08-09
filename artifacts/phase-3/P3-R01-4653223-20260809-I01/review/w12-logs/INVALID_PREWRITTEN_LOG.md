# Invalid prewritten W12 command records

`03-acceptance.log`, `03-acceptance.exit`, `04-validate-w12-receipt.log`, and `04-validate-w12-receipt.exit` were incorrectly written before the corresponding commands completed. They are retained unchanged as failed-process evidence and must not be treated as actual execution records.

The actual acceptance invocation is recorded in `05-actual-acceptance.log` and `05-actual-acceptance.exit`. It failed with exit code 5 because the final manifest schema rejected a zero-byte listed file. W12 receipt validation was not executed because no W12 receipt was created.
