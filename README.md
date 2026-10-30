# controlplane-events

Component packaging from Panopticon source `2d92d13f38d491c298631b9b9296979c1fbadd19`.

`src/` is relocated from `core/events/src`; runtime and wire/schema bytes
are preserved unless an explicit change is recorded in `SOURCE-MANIFEST.json`.
`EXTRACTION.json` and the Git parents retain the original selected-path history.

Run `bash scripts/check.sh`. It uses the frozen dependency lock, runs the
component contracts, builds wheel/sdist, then installs the wheel and locked
dependencies into a fresh environment and repeats tests outside this checkout.
No sibling repository or monorepo environment is used for base package checks.

Private CI has read-only repository permissions and self-hosted Linux/x64 runners.
No App credential, deployment environment, package publication or runtime cutover
is introduced. Package metadata versions retain their imported values; they do
not reset or stand in for public-contract versions.

## Isolated component CI

Checks run on disposable GitHub-hosted Ubuntu workers for PRs, main and manual
runs. This repository has no access to the organization self-hosted runner groups,
so PR changes cannot select the shared private fleet. The contents token is
read-only; checkout credentials are not persisted, and no environment/deployment
secrets are provided. Only the two full-SHA checkout/uv setup actions are allowed
by repository policy; other actions are refused. No VM, runner or App is created.

This is the user-authorized isolation correction for new component repositories.
Existing private deployment and payment jobs retain their runner access. An
isolated package check does not authorize publication or runtime cutover.
