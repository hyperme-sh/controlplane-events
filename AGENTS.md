# Package development

Run bash scripts/check.sh for source, wheel and source-distribution checks.
Keep public contracts, wire bytes and package versions stable for packaging changes.
Do not import service implementations or search parent checkouts for dependencies.

CI uses isolated ubuntu-24.04 workers, exact action SHAs, read-only contents and
no deployment credentials. Migration coordination and receipts live in the
internal hypermesh-docs repository; describe durable development behavior here.

## Package publication permission boundary

Source/package validation keeps its existing runner policy and read-only contents.
The separate `.github/workflows/release.yml` publication job is the narrow exception:
it may use `contents: write` and `actions: read` on disposable `ubuntu-24.04` workers
solely through the exact-pinned shared package publisher. It must not check out or
execute this repository's code, install its packages, or receive private dependency
or deployment credentials. This hosted publication exception does not authorize
moving secret-bearing validation to another runner group. Stable publication must
retain any configured release-admission prerequisite for the exact tag/commit.
