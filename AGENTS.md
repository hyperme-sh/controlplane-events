# Component repository

Own the package under `src/`. Run `bash scripts/check.sh` for source, wheel and
standalone contract validation. Preserve the imported wire/API contracts and
version train. `EXTRACTION.json` records the original history import;
`SOURCE-MANIFEST.json` records runtime relocation and reviewed changes.
Do not import service/product implementations or rely on a parent monorepo.
Component PR/main checks run on disposable GitHub-hosted Ubuntu workers.
This repo must not receive access to shared self-hosted runner groups. Keep the
read-only token, disabled checkout persistence and exact action commit allowlist.
Do not add deployment/environment secrets to component checks.
A package build does not authorize publishing, deployment or consumer cutover.
