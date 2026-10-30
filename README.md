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
