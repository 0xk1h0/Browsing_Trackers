# Filter-list snapshots — upstream licenses

Each file in this directory is a *snapshot* (March 2026) of a publicly
available filter list maintained by a third party. The snapshots ship under
each list's original license. None of the upstream contributors are named
in the snapshots.

| File                       | Upstream                                                | License                       | Snapshot date |
|----------------------------|---------------------------------------------------------|-------------------------------|---------------|
| `disconnect.txt`           | Disconnect.me services.json                             | GPL-3.0                       | 2026-03       |
| `easylist.txt`             | EasyList (https://easylist.to/easylist/easylist.txt)    | GPL-3.0 / CC-BY-SA-3.0 (dual) | 2026-03       |
| `easyprivacy.txt`          | EasyPrivacy (easylist.to/easylist/easyprivacy.txt)      | GPL-3.0 / CC-BY-SA-3.0 (dual) | 2026-03       |
| `fanboy_enhanced.txt`      | Fanboy's Enhanced Tracking List                         | GPL-3.0 / CC-BY-SA-3.0 (dual) | 2026-03       |
| `adguard_tracking.txt`     | AdGuard Tracking Protection (filters/3.txt)             | GPL-3.0                       | 2026-03       |
| `peter_lowe.txt`           | Peter Lowe's ad+tracker list (pgl.yoyo.org)             | Free-for-research, attribution required | 2026-03 |
| `ublock_privacy.txt`       | uBlock Origin Privacy filters                           | GPL-3.0                       | 2026-03       |

## Pre-built lookup table

| File                            | Purpose                                                   |
|---------------------------------|-----------------------------------------------------------|
| `tracker_domains_7list.json`    | Aggregated host → list membership lookup table; for each host present in ≥1 list, records the subset of the seven lists that flag it. Generated from the seven `.txt` snapshots above. |

## Reproducing the snapshots

These snapshots were downloaded directly from each project's published URL
on 2026-03-15. To verify a snapshot against the upstream, fetch the current
list and diff:

```bash
curl -fsSL https://easylist.to/easylist/easylist.txt > /tmp/easylist.txt
diff easylist.txt /tmp/easylist.txt   # non-empty after upstream updates; expected
```

We freeze the snapshots at the publication date so the classifier results
in `data/*` are reproducible from the same input.
