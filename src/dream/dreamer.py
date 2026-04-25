"""
Dreamer orchestrator + CLI — manual-trigger entry point for stage 2.

Usage (from repo root):
    python -m dream.dreamer --days 3 --user owner
    python -m dream.dreamer --days 7                 # all users
    python -m dream.dreamer --dry-run                # don't write candidates
    python -m dream.dreamer --phase1-only            # only cluster, no Phase 2
    python -m dream.dreamer --phase2-model openrouter/anthropic/claude-sonnet-4.6
    python -m dream.dreamer --model openrouter/openai/gpt-5.4-mini   # both phases

What it does:
    1. Load user queries from the last --days days of sessions.
    2. Phase 1 LLM call: cluster queries into candidate patterns.
    3. For each pattern, slim its sessions and run Phase 2 LLM call.
    4. For patterns where the dreamer judges a skill is worth saving,
       write `skills/_candidates/<skill_name>/SKILL.md`.

No scheduling — you run this by hand until the output is trustworthy.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make `python -m dream.dreamer` work from repo root by ensuring src/ is on path.
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from config import DREAM_PHASE1_MODEL, DREAM_PHASE2_MODEL, SESSIONS_DIR  # noqa: E402
from core.llm import call_llm  # noqa: E402

from dream.prompts import (  # noqa: E402
    PHASE1_SYSTEM,
    PHASE2_SYSTEM,
    format_phase1_user,
    format_phase2_user,
)
from dream.session_reader import (  # noqa: E402
    QueryRecord,
    iter_recent_queries,
    load_session_by_conv_id,
)
from dream.slim import slim_session  # noqa: E402

logger = logging.getLogger("dream.dreamer")

# Candidate skills land here so they never silently pollute the live skill set.
CANDIDATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "skills",
    "_candidates",
)

_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _extract_json_block(text: str):
    """Extract the first ```json ... ``` block from the model's reply.

    Falls back to raw JSON if no fenced block is found.
    """
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    raw = m.group(1) if m else text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Could not parse JSON from model output: %s\n---\n%s", e, text[:500])
        return None


def _run_llm(system: str, user: str, model: str) -> str:
    """One-shot LLM call — system + user, no tools, no streaming."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    logger.debug("LLM call → model=%s", model)
    completion = call_llm(messages, model=model, tools=None)
    return completion.choices[0].message.content or ""


# ─────────────────────────── Phase 1 ───────────────────────────

def phase1_cluster(queries: list[QueryRecord], model: str | None = None) -> list[dict]:
    """Cluster user queries into candidate patterns. Returns [] on parse failure."""
    if not queries:
        return []
    user_msg = format_phase1_user(queries)
    chosen = model or DREAM_PHASE1_MODEL
    logger.info("Phase 1: %d queries → dreamer (model=%s)", len(queries), chosen)
    reply = _run_llm(PHASE1_SYSTEM, user_msg, model=chosen)
    parsed = _extract_json_block(reply)
    if not isinstance(parsed, list):
        logger.warning("Phase 1 returned non-list JSON: %r", type(parsed).__name__)
        return []

    # Sanitize: drop hallucinated conv_ids and single-session clusters.
    # The prompt asks for ≥2 distinct conv_ids; this is a belt-and-suspenders
    # filter in case the model ignores the rule.
    known_ids = {q.conv_id for q in queries}
    clean: list[dict] = []
    dropped_single = 0
    for p in parsed:
        if not isinstance(p, dict):
            continue
        conv_ids = sorted({c for c in (p.get("conv_ids") or []) if c in known_ids})
        if len(conv_ids) < 2:
            dropped_single += 1
            continue
        clean.append({
            "pattern_name": p.get("pattern_name") or "unnamed",
            "summary": p.get("summary") or "",
            "signals": p.get("signals") or "",
            "conv_ids": conv_ids,
        })
    if dropped_single:
        logger.info("Phase 1: dropped %d single-session clusters", dropped_single)
    logger.info("Phase 1: %d clusters accepted", len(clean))
    return clean


# ─────────────────────────── Phase 2 ───────────────────────────

def phase2_distill(pattern: dict, model: str | None = None) -> dict | None:
    """Load slim sessions for a pattern and ask the dreamer for a SKILL.md.

    Returns the parsed JSON object, or None on failure.
    """
    slim_sessions: list[dict] = []
    for conv_id in pattern["conv_ids"]:
        session = load_session_by_conv_id(conv_id)
        if session is None:
            logger.warning("Phase 2: missing session %s", conv_id)
            continue
        slim_sessions.append(slim_session(session))

    if not slim_sessions:
        return None

    user_msg = format_phase2_user(pattern, slim_sessions)
    chosen = model or DREAM_PHASE2_MODEL
    logger.info("Phase 2: %s → dreamer (model=%s)", pattern.get("pattern_name"), chosen)
    reply = _run_llm(PHASE2_SYSTEM, user_msg, model=chosen)
    parsed = _extract_json_block(reply)
    if not isinstance(parsed, dict):
        logger.warning("Phase 2 returned non-dict JSON for %s", pattern.get("pattern_name"))
        return None
    return parsed


# ─────────────────────────── Output ───────────────────────────

def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "unnamed"


def write_candidate(result: dict, pattern: dict) -> str | None:
    """Write a candidate SKILL.md under skills/_candidates/<slug>/.

    Returns the file path written, or None if the result is not worth saving.
    """
    if not result.get("worth_saving"):
        return None
    md = result.get("skill_md")
    if not isinstance(md, str) or not md.strip():
        return None

    slug = _slugify(result.get("skill_name") or pattern.get("pattern_name"))
    os.makedirs(CANDIDATES_DIR, exist_ok=True)
    skill_dir = os.path.join(CANDIDATES_DIR, slug)
    os.makedirs(skill_dir, exist_ok=True)
    out_path = os.path.join(skill_dir, "SKILL.md")

    # If the file already exists (re-run), suffix with timestamp to avoid clobber.
    if os.path.exists(out_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(skill_dir, f"SKILL.{ts}.md")

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(md if md.endswith("\n") else md + "\n")
    return out_path


# ─────────────────────────── Orchestration ───────────────────────────

def run_dream(
    days: int = 3,
    user_id: str | None = None,
    dry_run: bool = False,
    phase1_only: bool = False,
    phase1_model: str | None = None,
    phase2_model: str | None = None,
) -> dict:
    """Run one dream pass. Returns a summary dict for logging/debugging."""
    queries = list(iter_recent_queries(days=days, user_id=user_id))
    summary: dict = {
        "window_days": days,
        "user_id": user_id,
        "query_count": len(queries),
        "patterns": [],
        "candidates_written": [],
    }

    if not queries:
        logger.info("No queries in the last %d days; nothing to dream about.", days)
        return summary

    clusters = phase1_cluster(queries, model=phase1_model)
    summary["patterns"] = clusters

    if phase1_only:
        logger.info("--phase1-only set: skipping Phase 2.")
        return summary

    for pattern in clusters:
        result = phase2_distill(pattern, model=phase2_model)
        if result is None:
            continue
        pattern["phase2_result"] = {
            "worth_saving": result.get("worth_saving"),
            "reason": result.get("reason"),
            "skill_name": result.get("skill_name"),
        }
        if dry_run:
            continue
        path = write_candidate(result, pattern)
        if path:
            summary["candidates_written"].append(path)
            logger.info("Wrote candidate skill: %s", path)

    return summary


# ─────────────────────────── CLI ───────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run a dreamer pass over recent sessions.")
    p.add_argument("--days", type=int, default=3, help="Look-back window in days (default: 3).")
    p.add_argument("--user", default=None, help="Filter by user_id (default: all users).")
    p.add_argument("--dry-run", action="store_true", help="Run both phases, but don't write candidates.")
    p.add_argument("--phase1-only", action="store_true", help="Stop after Phase 1 (only inspect cluster output, no Phase 2 LLM calls).")
    p.add_argument("--phase1-model", default=None, help="Override Phase 1 model (default: config.DREAM_PHASE1_MODEL).")
    p.add_argument("--phase2-model", default=None, help="Override Phase 2 model (default: config.DREAM_PHASE2_MODEL).")
    p.add_argument("--model", default=None, help="Shortcut: override BOTH phases with one model.")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger.info("Dreamer starting — sessions_dir=%s days=%d", SESSIONS_DIR, args.days)
    # --model is a shortcut that pins both phases to the same model.
    p1_model = args.phase1_model or args.model
    p2_model = args.phase2_model or args.model
    summary = run_dream(
        days=args.days,
        user_id=args.user,
        dry_run=args.dry_run,
        phase1_only=args.phase1_only,
        phase1_model=p1_model,
        phase2_model=p2_model,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
