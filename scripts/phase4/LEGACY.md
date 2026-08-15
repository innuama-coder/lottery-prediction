# Superseded Phase 4 tooling

`validate_contract_bundle.py`, `bootstrap_release_environment.py`,
`benchmark_prequalification.py`, and `formal_qualification.py` belong to the old
T00–T24 preparation workflow. They are retained to preserve history, but are not
canonical D00–D15 acceptance commands and may require an unavailable immutable
preparation release.

The current contract command is `scripts/phase4/validate_real_model_contracts.py`.
The current formal path uses `build_real_model_release.py`, the independent
`replay_real_model_release.py`, and `finalize_real_model_release.py`.
