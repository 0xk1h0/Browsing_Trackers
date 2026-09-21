# golden/ — reference snapshot for `verify.py`

Read-only copies of every text artifact that `REPRODUCE.sh` regenerates, mirroring
the repo layout. The per-RQ scripts overwrite their own `expected_outputs/` (and, for
RQ3, `rq3_ablation/data/`), so this snapshot is what `verify.py` diffs the freshly
written files against. No git history required — a zip/tarball download verifies the
same way.

These are the camera-ready numbers. Authors refresh them deliberately with
`python3 verify.py --update`; nothing else should ever write here.
