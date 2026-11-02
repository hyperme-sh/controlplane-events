# Package development

Run bash scripts/check.sh for source, wheel and source-distribution checks.
Keep public contracts, wire bytes and package versions stable for packaging changes.
Do not import service implementations or search parent checkouts for dependencies.

CI uses isolated ubuntu-24.04 workers, exact action SHAs, read-only contents and
no deployment credentials. Migration coordination and receipts live in the
internal hypermesh-docs repository; describe durable development behavior here.
