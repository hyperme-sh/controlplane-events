# Component repository

Own the package under `src/`. Run `bash scripts/check.sh` for source, wheel and
standalone contract validation. Preserve the imported wire/API contracts and
version train. `EXTRACTION.json` records the original history import;
`SOURCE-MANIFEST.json` records runtime relocation and reviewed changes.
Do not import service/product implementations or rely on a parent monorepo.
Actions stay disabled until reviewed-main controls and runner isolation are verified.
Do not execute unmerged PR code on shared self-hosted runners. Staged main-only
private checks receive no deployment secrets.
A package build does not authorize publishing, deployment or consumer cutover.
