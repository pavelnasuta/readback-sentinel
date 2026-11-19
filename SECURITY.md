# Security and privacy policy

## Scope

Readback Sentinel is intended to run locally. Its likely inputs — narration audio, books, scripts, glossaries, transcripts and editorial comments — may be confidential, copyrighted or personally sensitive.

## Do not publish

Never put any of the following in a public issue, pull request, commit, release attachment or example fixture:

- customer audio or video;
- source books, scripts, PDFs, DOCX files or excerpts that are not explicitly licensed for publication;
- generated transcripts, timestamps, vectors or review results;
- client names, narrator names, contact data or credentials;
- model cache paths, API tokens, `.env` files or private configuration receipts.

The repository `.gitignore` excludes common audio, document and output formats. Ignore rules are not a security control: always review `git status` before committing.

## Reporting a vulnerability

For vulnerabilities that could expose input material, bypass a review binding, create narrator-facing output without confirmation, or execute untrusted paths, do **not** open a public issue.

Contact the maintainer privately through the GitHub account [@pavelnasuta](https://github.com/pavelnasuta) with:

- a concise description of the impact;
- affected version or commit;
- safe reproduction steps using synthetic data only;
- any suggested mitigation.

Do not attach real audio, books, reports or credentials.

## Supported versions

This is a preview project. Security and privacy fixes are applied to the default branch. Older tags may not receive backports.

## Operational cautions

- Run each analysis in a protected output directory separate from source materials.
- Treat generated transcripts and private error receipts as sensitive by default.
- Verify hashes and review bindings before confirming a candidate.
- No output should be sent to a narrator without editor confirmation.
