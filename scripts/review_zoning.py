"""List pending source evidence or record a decision on a current revision."""
import argparse
import json

from zoning_research import cache

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--revision", type=int)
parser.add_argument("--decision", choices=("reviewed", "rejected"))
parser.add_argument("--note")
args = parser.parse_args()
if args.revision is None:
    print(json.dumps(cache().queue(), indent=2, ensure_ascii=False))
elif not args.decision or not args.note:
    parser.error("--revision requires --decision and --note")
elif not cache().review(args.revision, args.decision, args.note):
    parser.error("The revision has changed, could not be refreshed, or was already reviewed")
