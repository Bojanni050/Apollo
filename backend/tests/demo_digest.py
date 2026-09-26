"""Ad-hoc visual check of the structural digest, run manually.

Kept out of the test suite because its value is in what it *prints*: it shows
that a long document's closing section survives a small budget.
"""
from __future__ import annotations

from app.services.markdown_structure import build_representation

LONG = (
    "# Architecture\n\nSome intro.\n"
    + "".join(
        f"\n## Section {i}\n\nContent for section {i} here. " + "filler text " * 200 + "\n"
        for i in range(40)
    )
    + "\n## Final Conclusion\n\nTHE DECISION WAS TO ADOPT POSTGRESQL.\n"
)


def main() -> None:
    for budget in (200, 400, 1_000):
        representation, mode = build_representation(LONG, budget)
        print(
            f"budget={budget:>5} mode={mode:<7} "
            f"end_visible={'ADOPT POSTGRESQL' in representation} "
            f"start_visible={'Section 0' in representation} "
            f"outline_complete={'Section 39' in representation} "
            f"approx_tokens={len(representation) // 4}"
        )
    print("\n--- digest at budget=400 ---")
    print(build_representation(LONG, 400)[0])


if __name__ == "__main__":
    main()
