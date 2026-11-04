# Agent conventions

## Project layout

- Every new file under `src/` must be claimed by a module in
  `docs/modules.yaml` in the same change that adds it; the `supply-chain`
  CI job (`robotsix-modules check-registration docs/modules.yaml --root .`)
  rejects unclaimed files.
