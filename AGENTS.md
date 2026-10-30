# Component repository

Own the package under `src/`. Run `bash scripts/check.sh` for source, wheel and
standalone contract validation. Preserve the imported wire/API contracts and
version train. `EXTRACTION.json` records the original history import;
`SOURCE-MANIFEST.json` records runtime relocation and reviewed changes.
Do not import service/product implementations or rely on a parent monorepo.
Private CI uses self-hosted linux/x64 runners and receives no deployment secrets.
A package build does not authorize publishing, deployment or consumer cutover.
