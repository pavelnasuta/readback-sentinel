# Contributing to Readback Sentinel

Thank you for improving a tool that may influence how people review creative work. The core rule is simple: **a model observation is not a verdict about a narrator.**

## Before you start

1. Search existing issues and pull requests.
2. Open an issue or write a short proposal describing one concrete hypothesis.
3. Keep customer media, books, transcripts, glossaries, model caches and generated reports outside the repository.
4. Do not change a classification rule just to reduce the number of flags. A lower flag count is not evidence of better QC.

## Local setup

```bash
uv sync --dev
uv run pytest -q
```

The public test suite must remain synthetic. It proves code behavior and safety semantics; it does not validate recognition accuracy on real recordings.

## Change process

1. Add a regression test or a synthetic evaluation case that demonstrates the problem.
2. Run it and verify the expected failure.
3. Implement the smallest change that satisfies the test.
4. Run the affected tests and the full suite.
5. Update `CHANGELOG.md` and the appropriate release note if behavior or an output contract changes.
6. Explain user-visible impact, limits and rollback in the pull request.

## Pull request checklist

- [ ] No customer, proprietary or personal data is included.
- [ ] Tests demonstrate the changed behavior.
- [ ] The full synthetic suite passes locally.
- [ ] New output is described as a candidate, observation or unavailable state — never an unsupported quality verdict.
- [ ] Documentation and release notes match the actual behavior.
- [ ] No model, dependency or runtime download is silently introduced.

## Scope boundaries

Good contributions include conservative alignment, evidence provenance, technical event detection, reproducible local runs and clearer reviewer artifacts.

Out of scope without a separate proposal: production hosting, user upload portals, cloud processing of customer media, automatic narrator messaging, arbitrary “expressiveness” scoring and claims of accuracy without labelled evaluation data.

## Reporting sensitive issues

Do not file public issues containing customer data, secrets or private reports. Follow [SECURITY.md](SECURITY.md).
