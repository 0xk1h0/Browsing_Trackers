#!/usr/bin/env python3
"""Generate participant_assignments.json with a fixed 10-task list shared by
every participant.

Replaces the previous stratified-random assignment. Every participant gets the
SAME 10 tasks in the same order, so we can compare human-vs-agent on a
matched task set.

Outputs:
    user_study/participant_assignments.json  (overwrites)

Usage:
    python build_fixed_tasks.py [--n-participants 30]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


STUDY_DIR = Path(__file__).resolve().parent.parent
OUT_PATH = STUDY_DIR / "participant_assignments.json"


# Per-task answer scaffold: pre-filled into the participant's textarea so
# answers come back in a uniform structure (easier to grade + less effort
# for the participant). Lines start with "a)/b)/..." prompts that the
# participant fills in next to the colon.
TASKS = [
    {
        "task_id": "Google Search--28",
        "domain": "Google Search",
        "start_url": "https://www.google.com/",
        "category": "information-lookup",
        "question": 'On Google Search, find the IMDb score and the Metacritic score of the movie "Inception".',
        "answer_template": "a) IMDb score:\nb) Metacritic score:",
    },
    {
        "task_id": "Google Map--35",
        "domain": "Google Maps",
        "start_url": "https://www.google.com/maps/",
        "category": "map-and-location",
        "question": "On Google Maps, search for Big Bend National Park and report the place type, rating, and the address (or a short description).",
        "answer_template": "a) Place type:\nb) Rating:\nc) Address or short description:",
    },
    {
        "task_id": "Amazon--32",
        "domain": "Amazon",
        "start_url": "https://www.amazon.com/",
        "category": "search-and-filter",
        "question": "On Amazon, find one electric kettle with capacity >= 1.5 L, stainless steel build, and 4-star rating or higher. Report its name, price, and rating. (Do NOT purchase or add to cart.)",
        "answer_template": "a) Product name:\nb) Price:\nc) Rating:",
    },
    {
        "task_id": "Apple--29",
        "domain": "Apple",
        "start_url": "https://www.apple.com/",
        "category": "information-lookup",
        "question": "On the Apple website, look up the latest MacBook Air's Tech Specs and report the estimated battery life on wireless web.",
        "answer_template": "a) Estimated battery life (wireless web):",
    },
    {
        "task_id": "Google Flights--34",
        "domain": "Google Flights",
        "start_url": "https://www.google.com/travel/flights/",
        "category": "search-and-filter",
        "question": "On Google Flights, search a Lisbon -> Singapore trip departing Dec 7 and returning Dec 11. Pick one flight, then report the chosen airline/time AND the booking websites shown for it. (You do NOT need to actually book.)",
        "answer_template": "a) Chosen flight (airline / time):\nb) Booking websites (>=1):",
    },
    {
        "task_id": "Booking--3",
        "domain": "Booking.com",
        "start_url": "https://www.booking.com/",
        "category": "booking-and-transaction",
        "question": "On Booking.com, find a 4-star (or higher) hotel in Los Angeles for the stay from Dec 7 to Dec 11 (4 nights). Report the hotel name, star rating, and rating/review score.",
        "answer_template": "a) Hotel name:\nb) Star rating:\nc) Rating or review score:",
    },
    {
        "task_id": "Coursera--21",
        "domain": "Coursera",
        "start_url": "https://www.coursera.org/",
        "category": "search-and-filter",
        "question": "On Coursera, find one beginner-level Digital Marketing course. Report the course title, expected duration, the offering institution, and the main learning outcomes. (You do NOT need to log in or enroll.)",
        "answer_template": "a) Course title:\nb) Expected duration:\nc) Offering institution:\nd) Main learning outcomes:",
    },
    {
        "task_id": "GitHub--35",
        "domain": "GitHub",
        "start_url": "https://github.com/",
        "category": "information-lookup",
        "question": "On GitHub, find the React repository and report the latest release version and its publish date.",
        "answer_template": "a) Latest release version:\nb) Publish date:",
    },
    {
        "task_id": "Huggingface--14",
        "domain": "Hugging Face",
        "start_url": "https://huggingface.co/",
        "category": "information-lookup",
        "question": "On the Hugging Face website, find the Pro account monthly price and at least two of its main features.",
        "answer_template": "a) Monthly price:\nb) Main features (>=2):",
    },
    {
        "task_id": "ArXiv--17",
        "domain": "ArXiv",
        "start_url": "https://arxiv.org/",
        "category": "information-lookup",
        "question": "On ArXiv, find the 'GPT-4 Technical Report' paper and report the date when v3 was submitted. (You do NOT need to download the PDF; use only the information shown on the abstract page.)",
        "answer_template": "a) v3 submission date:",
    },
]


def latin_square_order(pid_index: int, n_tasks: int) -> list[int]:
    """Return the per-participant ordering of TASKS indices.

    Uses a cyclic Latin square: for participant pid_index (1-indexed) the
    j-th task (0-indexed) is master-index ((pid_index - 1) + j) mod n_tasks.

    Properties (over n_tasks consecutive participants):
      - Each task appears exactly once in each ordinal position.
      - Each participant sees every task exactly once.

    Cyclic squares balance first-order position effects (task fatigue,
    learning, time-of-session attention drift). For higher-order
    counterbalancing (adjacency), a Williams' design would be preferable;
    cyclic is the standard prototype choice.
    """
    shift = (pid_index - 1) % n_tasks
    return [(shift + j) % n_tasks for j in range(n_tasks)]


def build(n_participants: int) -> dict:
    n_tasks = len(TASKS)
    assignments = []
    square_table = []  # for human-readable diagnostic in JSON

    for i in range(1, n_participants + 1):
        pid = f"P{i:03d}"
        order_indices = latin_square_order(i, n_tasks)
        tasks_for_pid = []
        for order_pos, master_idx in enumerate(order_indices, start=1):
            t = TASKS[master_idx]
            tasks_for_pid.append({
                "order": order_pos,
                "master_index": master_idx + 1,  # 1-indexed for human readability
                "task_id": t["task_id"],
                "benchmark": "webvoyager",
                "domain": t["domain"],
                "category": t["category"],
                "ad_profile": "moderate",
                "question": t["question"],
                "start_url": t["start_url"],
                "answer_template": t.get("answer_template", ""),
            })
        assignments.append({
            "participant_id": pid,
            "n_tasks": n_tasks,
            "latin_shift": (i - 1) % n_tasks,
            "task_order_master_indices": [m + 1 for m in order_indices],
            "categories": sorted({t["category"] for t in TASKS}),
            "ad_profiles": ["moderate"],
            "domains": sorted({t["domain"] for t in TASKS}),
            "tasks": tasks_for_pid,
        })
        square_table.append({
            "pid": pid,
            "order": [TASKS[m]["task_id"] for m in order_indices],
        })

    return {
        "design": {
            "type": "cyclic-latin-square",
            "n_participants": n_participants,
            "n_tasks": n_tasks,
            "rationale": (
                "Each participant performs the same 10 tasks but with a "
                "cyclic-shifted ordering. Across every block of 10 "
                "participants, every task appears exactly once in each "
                "ordinal position, counterbalancing first-order position "
                "effects (fatigue, learning, attention drift)."
            ),
        },
        "summary": {
            "n_participants": n_participants,
            "tasks_per_participant": n_tasks,
            "total_sessions": n_participants * n_tasks,
        },
        "tasks_master": TASKS,
        "latin_square": square_table,
        "assignments": assignments,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-participants", type=int, default=20)
    args = ap.parse_args()

    data = build(args.n_participants)
    OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[done] wrote {OUT_PATH}")
    print(f"  participants : {args.n_participants}")
    print(f"  tasks each   : {len(TASKS)}")
    print(f"  total slots  : {args.n_participants * len(TASKS)}")
    print(f"  design       : cyclic Latin square")
    print()
    print("  Task master list:")
    for j, t in enumerate(TASKS, start=1):
        print(f"    [{j:2d}] {t['task_id']:25s}  {t['domain']:20s}")
    print()
    print("  Latin-square assignment (one row per participant, columns = order 1..10):")
    print("       " + " ".join(f"{p:>3d}" for p in range(1, len(TASKS) + 1)))
    for a in data["assignments"][: min(args.n_participants, 10)]:
        order_idx = a["task_order_master_indices"]
        print(f"  {a['participant_id']:>5}  " + " ".join(f"{x:>3d}" for x in order_idx))
    if args.n_participants > 10:
        print("  ... (further participants reuse the same 10 cycle rows)")


if __name__ == "__main__":
    main()
