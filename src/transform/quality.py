"""Data quality checks that run before the pipeline writes anything.

Each check returns {"name", "passed", "detail"}. If any check fails, the pipeline
loads nothing and records the run as failed, with every check's result.
Works on any platform's normalized tables.
"""
import pandera.errors

from transform.schemas import markets_schema, snapshots_schema

MIN_MARKET_SHARE = 0.95  # a download with far fewer markets than the database already has is broken


def check(name, passed, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


def check_download_size(eligible_markets, markets_in_database, min_share=MIN_MARKET_SHARE):
    """Resolved markets don't disappear, so a much smaller download means something went wrong
    (an API change, an error page, a cut-off pagination) and must not be trusted."""
    minimum = int(markets_in_database * min_share)
    return check(
        "download_size",
        eligible_markets >= minimum,
        f"{eligible_markets} eligible markets downloaded; need at least {minimum} "
        f"({min_share:.0%} of the {markets_in_database} already in the database)",
    )


def check_schema(name, schema, table):
    """The table matches its pandera schema (columns, types, value ranges, uniqueness, no lookahead)."""
    try:
        schema.validate(table, lazy=True)  # lazy: collect every problem, not just the first
        return check(name, True, f"{len(table)} rows valid")
    except pandera.errors.SchemaErrors as error:
        problems = error.failure_cases["check"].astype(str).unique()[:5]
        return check(name, False, f"{len(error.failure_cases)} problems, e.g. {', '.join(problems)}")


def check_snapshots_match_markets(markets, snapshots):
    """Every new snapshot belongs to a new market (no orphans)."""
    orphans = set(snapshots["market_id"]) - set(markets["market_id"])
    return check("snapshots_match_markets", not orphans, f"{len(orphans)} snapshots without a market")


def run_checks(eligible_markets, markets_in_database, new_markets, new_snapshots):
    """Every check, in order. The pipeline loads only if all of them pass."""
    return [
        check_download_size(eligible_markets, markets_in_database),
        check_schema("markets_schema", markets_schema, new_markets),
        check_schema("snapshots_schema", snapshots_schema, new_snapshots),
        check_snapshots_match_markets(new_markets, new_snapshots),
    ]
