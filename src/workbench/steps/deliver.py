"""deliver: put files into the paper repository, planned, checked and verified; the human commits.

Sources:
- the workbench output tree ``data/workbench/<project>/out/`` (repository-relative paths), or
- ``--from-inventory``: the P6 freeze. Every INVENTORY row of the project with disposition deliver,
  private or code goes to its destination; scripts get a '# Provenance:' header. Mirror rows are
  only compared, never written.

A real delivery to the paper repository records the outcome in INVENTORY.csv (present / delivered;
mirrors present / differs / missing) and in the registry (last_delivery*). A delivery into a
scratch directory (``--to DIR``) records nothing.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from src.workbench import decommission as D
from src.workbench import registry
from src.workbench.delivery import (DELIVERY_DIR, Item, make_plan, private_root, provenance_header, render,
                                    summarize, write)
from src.workbench.steps import Context, project_dir, slug

FREEZE = D.FROZEN_DIR / D.DEFAULT_NAME


def inventory_items(project_id: str, freeze=FREEZE) -> tuple[list[Item], list[dict]]:
    commit, day = D.frozen_commit(freeze), date.today().isoformat()
    items, mirrors = [], []
    for r in D.read_inventory(freeze):
        if r["project"] != project_id or r["kind"] != "file":
            continue
        if r["disposition"] == "mirror":
            mirrors.append(r)
            continue
        if r["disposition"] not in ("deliver", "private", "code"):
            continue
        data = (freeze / r["path"]).read_bytes()
        if r["disposition"] == "code" and r["destination"].endswith((".py", ".sh")):
            data = provenance_header(data, r["path"], r["sha256"], commit, day)
        items.append(Item(r["destination"], data, f"frozen:{r['path']}", r["disposition"], r["path"],
                          mtime=(freeze / r["path"]).stat().st_mtime))
    return items, mirrors


def out_items(project_id: str) -> list[Item]:
    out = project_dir(project_id) / "out"
    items = []
    for f in sorted(p for p in out.rglob("*") if p.is_file()) if out.is_dir() else []:
        rel = f.relative_to(out).as_posix()
        items.append(Item(rel, f.read_bytes(), f"workbench:out/{rel}", "private" if private_root(rel) else "deliver"))
    return items


def compare_mirrors(remote, mirrors: list[dict], freeze=FREEZE) -> dict[str, str]:
    """present | superseded (theirs differs and is newer) | differs | missing, per freeze path."""
    state = remote.files([r["destination"] for r in mirrors])
    out = {}
    for r in mirrors:
        st = state.get(r["destination"], {})
        theirs = st.get("committed") or st.get("mtime")
        if st.get("sha256") == r["sha256"]:
            out[r["path"]] = "present"
        elif st.get("sha256") is None:
            out[r["path"]] = "missing"
        else:
            newer = theirs and theirs > (freeze / r["path"]).stat().st_mtime
            out[r["path"]] = "superseded" if newer else "differs"
    return out


def last_delivered(remote, project_id: str) -> dict[str, str]:
    """path -> sha256 of the newest delivery of this project to the repository (from .ghai/deliveries)."""
    suffix = f"_{slug(project_id)}.json"
    names = sorted(n for n in remote.listdir(DELIVERY_DIR) if n.endswith(suffix))
    if not names:
        return {}
    doc = json.loads(remote.read(f"{DELIVERY_DIR}/{names[-1]}") or b"{}")
    return {f["path"]: f["sha256"] for f in doc.get("files", []) if f.get("action") in ("new", "update", "same")}


def run(ctx: Context, *, from_inventory: bool = False, dry_run: bool = False, force: bool = False) -> int:
    m = ctx.manifest()
    if from_inventory:
        items, mirrors = inventory_items(ctx.project_id)
    else:
        items, mirrors = out_items(ctx.project_id), []
    if not items and not mirrors:
        print(f"{ctx.project_id}: nothing to deliver")
        return 0
    previous = None if from_inventory else last_delivered(ctx.remote, ctx.project_id)
    plan = make_plan(ctx.project_id, ctx.remote, items, public=ctx.public, never=tuple(m.blocked()) if m else (),
                     force=force, last_delivered=previous)
    print(render(plan))
    mirror_state = compare_mirrors(ctx.remote, mirrors) if mirrors else {}
    if mirror_state:
        counts = {s: sum(1 for v in mirror_state.values() if v == s)
                  for s in ("present", "superseded", "differs", "missing")}
        print(f"  mirrors (the paper repository is the source; compared only): {counts}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record_dir = project_dir(ctx.project_id) / "deliveries" / (stamp + ("-dryrun" if dry_run else ""))
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "plan.json").write_text(json.dumps({**summarize(plan, stamp), "target": plan.target,
                                                      "mirrors": mirror_state}, indent=1, ensure_ascii=False),
                                          encoding="utf-8")
    unresolved = len(plan.by_kind("conflict", "blocked"))
    if dry_run:
        print(f"dry run: nothing written; plan saved to {record_dir.relative_to(D.ROOT)}")
        return 2 if unresolved else 0

    result = write(plan, ctx.remote, stamp=stamp)
    (record_dir / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written {result['written']} files; manifest {result['manifest']}; remote check: "
          f"{'OK' if result.get('manifest') else 'nothing to check'}")
    if not ctx.local_target:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if from_inventory:
            updates = {}
            for a in plan.actions:
                if not a.item.inventory:
                    continue
                if a.kind in ("same", "new", "update"):
                    updates[a.item.inventory] = {"status": "present" if a.kind == "same" else "delivered",
                                                 "checked_at": now}
                elif a.superseded:
                    updates[a.item.inventory] = {"status": "superseded", "checked_at": now, "note": a.reason}
            for path, state in mirror_state.items():
                updates[path] = {"status": state, "checked_at": now}
            D.update_rows(FREEZE, updates)
        registry.record_delivery(ctx.project_id, {"stamp": stamp, "counts": plan.counts(),
                                                  "manifest": result.get("manifest")},
                                 plan.consumer.get("commit"))
    return 2 if unresolved else 0
