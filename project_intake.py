"""
project_intake.py — P3-7 (Jake 2026-11-02): a project added by a YAML file, beside config.py.

    projects/_intake_template.yaml   the commented template (files starting with '_' are never loaded)
    projects/<slug>.yaml             one project; `status: draft` is validated but NOT loaded into builds,
                                     `status: active` is appended to config.PROJECTS (the existing projects stay
                                     in config.py — nothing is migrated)

EVERY FIELD IS `{value: ..., source: ...}`. `source` is an http(s) URL, or the literal `TODO: <what is needed>`. A
TODO field's value never reaches config (an unsourced number is not a datum); the metrics it feeds carry a
`{metric}_blocked` record (status "not wired", WANTED: the TODO text) — the record fetch/gaps.py already turns into the
Gap Report reason, which the build writes on the Data row and the reader shows as the code "not wired" with that reason
(reader.code_of). So a TODO reads on the workbook as a code with its reason, through the existing path.

config.py calls `active_projects(PROJECTS, globals())` once, just before PROJECT_BY_NAME is formed, so every later
module-level pass over PROJECTS (the price handover, the only_projects sets) covers a YAML project exactly as it covers
a config.py project. This module imports nothing from config (config imports it).

TOKEN_METRICS_PROJECTS_DIR overrides the folder (tests; a staging folder). PyYAML is already a dependency
(requirements.txt; fetch/scrape.py reads sources.yaml with it).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUSES = ("draft", "active")
DESTINATIONS = {"burn": "removed_from_supply", "hold": "treasury_redeployable", "distribute": "yield_payout",
                "none": "none"}
EMISSION_MODELS = ("minted", "distributed_from_premint", "vesting_complete", "none")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

# WHICH METRICS EACH YAML FIELD FEEDS — the intake's own mapping (config.py's equivalent is the key itself: a
# `coingecko_id` feeds CoinGecko's series, a `noncirc_*` contract feeds noncirculating_holding_tokens). Used for the
# `{metric}_blocked` record of a TODO field and by add_project.py's forecast (which input answers which metric).
FIELD_METRICS = {
    "coingecko_id": ("price_usd", "market_cap_usd", "volume_usd", "circulating_supply", "fdv_usd"),
    "defillama.fees_slug": ("fees_usd",),
    "defillama.revenue_slug": ("revenue_usd",),
    "defillama.holders_revenue_slug": ("holders_revenue_usd",),
    "defillama.tvl_slug": ("protocol_tvl_usd",),
    "chains": ("total_supply",),
    "max_supply": ("max_supply",),
    "noncirculating_wallets": ("noncirculating_holding_tokens",),
    "unlock_schedule": ("unlock_release_tokens",),
    "emission": ("emissions_tokens", "gross_issuance_tokens"),
    "buyback": ("actual_buyback_tokens", "actual_buyback_usd"),
    "staking": ("locked_tokens",),
    "insider_wallets": ("insider_stake_shares",),
}
# What a YAML can say that config has no slot for YET (recorded under project["intake"], read by add_project.py only).
NOT_EXPRESSIBLE = {
    "chains[1:]": "a second chain's token is probed, not summed into total_supply (config sums chains per project by "
                  "a declared series_handover / chain:sum point, which the intake does not write)",
    "unlock_schedule": "no config structure turns a dated unlock list into unlock_release_tokens; recorded only",
    "insider_wallets": "insider stake needs the issuer-wallet audit (insider_stake_* contracts + INSIDER_* tables); "
                       "recorded only",
    "unit_of_demand": "BUSINESS_SLOTS is declared later in config.py than the loader hook; recorded only",
    "buyback.revenue_share": "a share is written to fee_split.share_to_buyback; a per-product or dated split "
                             "(BUYBACK_DESTINATION_SHARES) is not expressible",
    "defillama.revenue_slug/holders_revenue_slug": "config reads fees, revenue and holders revenue from ONE slug "
                                                   "(defillama_fees_slug); a different slug is refused",
}


class IntakeError(ValueError):
    """A YAML project that cannot be loaded, with the file and the field named."""


def projects_dir() -> Path:
    return Path(os.environ.get("TOKEN_METRICS_PROJECTS_DIR") or ROOT / "projects")


def yaml_files(folder: Path | None = None) -> list[Path]:
    """projects/*.yaml, files starting with '_' skipped, sorted (the load order is the file order)."""
    folder = folder or projects_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.yaml") if not p.name.startswith("_"))


def read(path: Path) -> dict:
    import yaml                                            # noqa: PLC0415 — only when a file exists
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise IntakeError(f"{Path(path).name}: not a YAML mapping")
    return doc


# ============================================================================================== fields
def is_field(x) -> bool:
    return isinstance(x, dict) and "value" in x


def is_todo(field: dict) -> bool:
    return str(field.get("source") or "").startswith("TODO:")


def todo_reason(field: dict) -> str:
    return str(field.get("source") or "")[len("TODO:"):].strip()


def fields(doc, path: str = "") -> list[tuple[str, dict]]:
    """Every field of the document as (dotted path, {value, source, ...}), lists indexed: chains[0].decimals."""
    out = []
    if is_field(doc):
        return [(path, doc)]
    if isinstance(doc, dict):
        for k, v in doc.items():
            out += fields(v, f"{path}.{k}" if path else str(k))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out += fields(v, f"{path}[{i}]")
    return out


def source_problems(doc: dict) -> list[str]:
    """Each field without an http(s) source or a `TODO: <reason>` (a bare 'TODO:' has no reason, so it fails)."""
    bad = []
    for path, f in fields({k: v for k, v in doc.items() if k not in ("status", "as_of")}):
        s = str(f.get("source") or "").strip()
        if s.startswith("TODO:"):
            if not todo_reason(f):
                bad.append(f"{path}: 'TODO:' without what is needed")
        elif not re.match(r"^https?://\S+", s):
            bad.append(f"{path}: no source (an http(s) URL, or 'TODO: <what is needed>')")
    return bad


def value(doc: dict, path: str, default=None):
    """The value of a SOURCED field (None for a TODO field or an absent one) — unsourced values never reach config."""
    node = doc
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            i = int(part[1:-1])
            node = node[i] if isinstance(node, list) and i < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return default
    if not is_field(node) or is_todo(node):
        return default
    return node["value"] if node["value"] is not None else default


def todos(doc: dict) -> list[tuple[str, str]]:
    """(field path, what is needed) for every TODO field."""
    return [(p, todo_reason(f)) for p, f in fields(doc) if is_todo(f)]


def _top(path: str) -> str:
    """The FIELD_METRICS key a field path falls under: chains[0].decimals -> chains, defillama.fees_slug -> itself."""
    if path.startswith("defillama."):
        return path
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def wallets(doc: dict, group: str) -> list:
    """A wallet list (noncirculating_wallets / insider_wallets): a list of wallets, or ONE field {value: [], source}
    saying there are none (with its source or TODO)."""
    w = doc.get(group)
    return [] if is_field(w) or not isinstance(w, list) else w


def validate(doc: dict, name_of_file: str = "") -> list[str]:
    """Every problem with a document (empty = loadable). Source rules, required fields, value shapes."""
    where = name_of_file or "project yaml"
    errs = [f"{where}: {e}" for e in source_problems(doc)]
    st = doc.get("status")
    if st not in STATUSES:
        errs.append(f"{where}: status must be one of {', '.join(STATUSES)} (got {st!r})")
    for req in ("name", "symbol", "coingecko_id", "defillama", "chains", "max_supply", "noncirculating_wallets",
                "unlock_schedule", "emission", "buyback", "staking", "insider_wallets", "archetypes",
                "unit_of_demand"):
        if req not in doc:
            errs.append(f"{where}: field `{req}` is missing (write it with `source: \"TODO: <what is needed>\"` "
                        "when it is not known)")
    if not value(doc, "name"):
        errs.append(f"{where}: `name` needs a sourced value")
    prim = value(doc, "archetypes.primary")
    if prim not in (1, 2, 3, 4):
        errs.append(f"{where}: archetypes.primary must be a sourced 1-4 (got {prim!r})")
    for i, _ in enumerate((doc.get("archetypes") or {}).get("secondary") or []):
        if value(doc, f"archetypes.secondary[{i}]") not in (1, 2, 3, 4, None):
            errs.append(f"{where}: archetypes.secondary[{i}] must be 1-4")
    ar = doc.get("archetypes") or {}
    for k, node in [("archetypes.primary", ar.get("primary"))] + [
            (f"archetypes.secondary[{i}]", n) for i, n in enumerate(ar.get("secondary") or [])]:
        if is_field(node) and not str(node.get("reason") or "").strip():
            errs.append(f"{where}: {k} needs a one-line `reason`")
    fees = value(doc, "defillama.fees_slug")
    for k in ("revenue_slug", "holders_revenue_slug"):
        v = value(doc, f"defillama.{k}")
        if v is not None and fees is not None and v != fees:
            errs.append(f"{where}: defillama.{k} {v!r} differs from fees_slug {fees!r} — config reads all three from "
                        "one slug (defillama_fees_slug); not expressible yet")
    chains = doc.get("chains") or []
    if not isinstance(chains, list) or not chains:
        errs.append(f"{where}: `chains` must list at least one chain")
    for i, c in enumerate(chains if isinstance(chains, list) else []):
        a = value(doc, f"chains[{i}].token_contract")
        if a is not None and not (_ADDR.match(str(a)) or str(c.get("standard", "")).startswith("spl")):
            errs.append(f"{where}: chains[{i}].token_contract {a!r} is not a 0x address")
        d = value(doc, f"chains[{i}].decimals")
        if d is not None and not (isinstance(d, int) and 0 <= d <= 36):
            errs.append(f"{where}: chains[{i}].decimals must be an integer")
    known = {value(doc, f"chains[{i}].chain") for i, _ in enumerate(chains if isinstance(chains, list) else [])}
    for group in ("noncirculating_wallets", "insider_wallets"):
        seen = set()
        for i, w in enumerate(wallets(doc, group)):
            if not isinstance(w, dict):
                errs.append(f"{where}: {group}[{i}] must be a mapping")
                continue
            if not _KEY.match(str(w.get("key") or "")) or w.get("key") in seen:
                errs.append(f"{where}: {group}[{i}].key must be a unique lower_snake id")
            seen.add(w.get("key"))
            if None not in known and w.get("chain") not in known:
                errs.append(f"{where}: {group}[{i}].chain {w.get('chain')!r} is not one of the declared chains")
            a = value(doc, f"{group}[{i}].address")
            if a is not None and not _ADDR.match(str(a)):
                errs.append(f"{where}: {group}[{i}].address {a!r} is not a 0x address")
    dest = value(doc, "buyback.destination")
    if dest is not None and dest not in DESTINATIONS:
        errs.append(f"{where}: buyback.destination must be one of {', '.join(DESTINATIONS)} (got {dest!r})")
    share = value(doc, "buyback.revenue_share")
    if share is not None and not (isinstance(share, (int, float)) and 0 <= share <= 1):
        errs.append(f"{where}: buyback.revenue_share must be a fraction 0-1")
    model = value(doc, "emission.model")
    if model is not None and model not in EMISSION_MODELS:
        errs.append(f"{where}: emission.model must be one of {', '.join(EMISSION_MODELS)}")
    return errs


# ============================================================================================== to config
def _blocked(doc: dict, slug: str) -> dict:
    """{metric}_blocked records for every metric a TODO field feeds (fetch/gaps._format_blocked shapes them)."""
    out: dict = {}
    for path, reason in todos(doc):
        for m in FIELD_METRICS.get(_top(path), ()):
            rec = out.setdefault(f"{m}_blocked", {
                "status": "not wired", "wanted": [], "source_url": f"projects/{slug}.yaml",
                "source_date": str(doc.get("as_of") or ""),
                "why": f"the intake file projects/{slug}.yaml has no source for the field(s) this figure needs",
                "route_that_would_work": f"fill the field(s) in projects/{slug}.yaml with a primary source URL, then "
                                         f"`python add_project.py {slug} --probe`"})
            rec["wanted"].append(f"TODO: {reason} ({path})")
    for rec in out.values():
        rec["wanted"] = "; ".join(rec["wanted"])
    return out


def to_project(doc: dict, slug: str, ns: dict) -> dict:
    """The PROJECTS entry for a document — the same keys a config.py entry carries (see Aave, peaq, Render).
    `ns` is config's namespace (its _contract, _dune and _NO_SPLIT helpers, so the shapes are config's own)."""
    _contract, _dune, no_split = ns["_contract"], ns["_dune"], ns["_NO_SPLIT"]
    name, sym = value(doc, "name"), value(doc, "symbol")
    as_of = str(doc.get("as_of") or "")
    prim = value(doc, "archetypes.primary")
    sec = [value(doc, f"archetypes.secondary[{i}]")
           for i, _ in enumerate((doc.get("archetypes") or {}).get("secondary") or [])]
    arch = [prim] + [a for a in sec if a and a != prim]
    contracts: dict = {}
    chains = doc.get("chains") or []
    main_chain = value(doc, "chains[0].chain")
    tok = value(doc, "chains[0].token_contract")
    if tok and main_chain and value(doc, "chains[0].decimals") is not None:
        contracts["token"] = _contract(tok, main_chain, "erc20_total_supply", sym,
                                       doc["chains"][0]["token_contract"]["source"], verified=as_of or None,
                                       provenance=f"projects/{slug}.yaml")
    for i, w in enumerate(wallets(doc, "noncirculating_wallets")):
        addr = value(doc, f"noncirculating_wallets[{i}].address")
        if not addr:
            continue
        label = value(doc, f"noncirculating_wallets[{i}].label") or w["key"]
        contracts[f"noncirc_{w['key']}"] = _contract(
            addr, w["chain"], "treasury_holding", sym, w["address"]["source"], verified=as_of or None,
            purpose=label, provenance=f"projects/{slug}.yaml", holder_has_code=None,
            metric_override="noncirculating_holding_tokens")
    stk = value(doc, "staking.contract")
    if stk:
        contracts["staking"] = _contract(stk, doc["staking"].get("chain") or main_chain, "ve_total_supply",
                                         value(doc, "staking.symbol") or f"st{sym}", doc["staking"]["contract"]["source"],
                                         verified=as_of or None, provenance=f"projects/{slug}.yaml",
                                         read_method=value(doc, "staking.read_method"),
                                         token_standard=value(doc, "staking.token_standard"),
                                         underlying="token" if "token" in contracts else None,
                                         purpose=value(doc, "staking.mechanism") or "")
    dest = value(doc, "buyback.destination")
    share = value(doc, "buyback.revenue_share")
    fee_split = dict(no_split)
    if dest and dest != "none":
        fee_split = {"share_to_buyback": share, "source_url": (doc["buyback"].get("revenue_share") or {}).get("source"),
                     "source_date": as_of, "programmed": None, "status": value(doc, "buyback.status") or "active",
                     "note": f"from projects/{slug}.yaml"}
    rate, start = value(doc, "emission.tokens_per_day"), value(doc, "emission.from")
    issuance = None
    if rate is not None and start:
        issuance = {"steps": [{"from": str(start), "tokens_per_day": float(rate)}],
                    "source_url": doc["emission"]["tokens_per_day"]["source"], "source_date": as_of, "status": "active",
                    "note": value(doc, "emission.mechanism") or ""}
    p = {
        "name": name, "symbol": sym,
        "coingecko_id": value(doc, "coingecko_id"),
        "defillama_fees_slug": value(doc, "defillama.fees_slug"),
        "defillama_protocol": value(doc, "defillama.tvl_slug"), "defillama_chain": None,
        "archetypes": arch, "archetypes_held": [],
        "fee_split": fee_split, "burn_split": None, "issuance_schedule": issuance,
        "contracts": contracts,
        "buyback_destination": dest if dest and dest != "none" else "n/a", "destination_split": None,
        "burn_execution": "protocol" if dest == "burn" else "n/a",
        "destination_effect": DESTINATIONS.get(dest or "none", "none"),
        "dune_queries": _dune(),
        "materiality": "low",
        "notes": f"Added by projects/{slug}.yaml (P3-7 intake), read {as_of}.",
    }
    if 1 in arch:
        p["is_chain"] = bool(value(doc, "is_chain"))
    model = value(doc, "emission.model")
    if model:
        p["emissions_model"] = {"model": model, "sourced": True, "source": doc["emission"]["model"]["source"],
                                "declared_by": f"projects/{slug}.yaml", "note": value(doc, "emission.mechanism") or ""}
    p.update(_blocked(doc, slug))
    # what config has no slot for yet travels with the project, for add_project.py and the reader of the file
    p["intake"] = {"file": f"projects/{slug}.yaml", "slug": slug, "status": doc.get("status"), "as_of": as_of,
                   "todos": todos(doc), "max_supply": value(doc, "max_supply"),
                   "chains": [{"chain": value(doc, f"chains[{i}].chain"),
                               "token": value(doc, f"chains[{i}].token_contract"),
                               "decimals": value(doc, f"chains[{i}].decimals")} for i, _ in enumerate(chains)],
                   "unlock_schedule": value(doc, "unlock_schedule"),
                   "insider_wallets": [(w.get("key"), w.get("chain"), value(doc, f"insider_wallets[{i}].address"))
                                       for i, w in enumerate(wallets(doc, "insider_wallets"))],
                   "buyback_address": value(doc, "buyback.address"),
                   "unit_of_demand": value(doc, "unit_of_demand"),
                   "archetype_reasons": {str(prim): ((doc.get("archetypes") or {}).get("primary") or {}).get("reason"),
                                         **{str(value(doc, f"archetypes.secondary[{i}]")): s.get("reason")
                                            for i, s in enumerate((doc.get("archetypes") or {}).get("secondary") or [])}}}
    return p


def load(folder: Path | None = None) -> list[tuple[Path, dict]]:
    """(path, document) for every project YAML in the folder; a file that fails validation raises IntakeError."""
    out = []
    for f in yaml_files(folder):
        doc = read(f)
        errs = validate(doc, f.name)
        if errs:
            raise IntakeError("PROJECT YAML NOT LOADED — fix the file or set it aside (rename it to start with '_'):\n  "
                              + "\n  ".join(errs))
        out.append((f, doc))
    return out


def active_projects(existing: list, ns: dict, folder: Path | None = None) -> list[dict]:
    """The PROJECTS entries of every `status: active` YAML. A draft is validated and skipped. A name config.py already
    declares is an error (no YAML replaces or extends a config.py project — the safe rule; every config.py entry is a
    full project, none a stub)."""
    have = {p["name"].casefold(): "config.py" for p in existing}
    out = []
    for f, doc in load(folder):
        if doc.get("status") != "active":
            continue
        name = value(doc, "name")
        if name.casefold() in have:
            raise IntakeError(f"PROJECT YAML NOT LOADED — {f.name}: the project {name!r} is already declared in "
                              f"{have[name.casefold()]}. A YAML adds a NEW project; it does not replace or extend an "
                              "existing entry. Keep the file as `status: draft`, or remove the other entry first (a "
                              "deliberate migration).")
        have[name.casefold()] = f.name
        out.append(to_project(doc, f.stem, ns))
    return out
