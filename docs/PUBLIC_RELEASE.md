# Public-release boundary

This repository is public so that its design and safety contracts can be inspected. It is **not** a publication channel for client material or a claim that automated narration assessment is solved.

## What a public clone can reproduce

A clone can install the Python environment, run the synthetic test suite, inspect the alignment and confirmation contracts, and adapt the local CLI to supplied private paths.

## What a public clone cannot establish

A clone alone does not establish:

- ASR accuracy for a specific language, genre, narrator or recording environment;
- precision, recall or false-positive rate on real defects;
- customer-approved technical thresholds;
- artistic, pronunciation or semantic judgement;
- server, Linux, long-file or throughput readiness;
- permission to analyse third-party recordings or books.

## Public examples

Examples must be synthetic or demonstrably redistributable. They may illustrate schemas and output shape, but must never be presented as quality evidence for real narration.

## Releasing changes

Before a public release:

1. Run the synthetic suite.
2. Review the staged diff for customer files, credentials, absolute private paths and generated outputs.
3. Document changed behavior and known limits.
4. Confirm that new outputs remain reviewer-facing unless bound human confirmation is present.
5. Tag only the exact reviewed commit.
