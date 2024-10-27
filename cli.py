#!/usr/bin/env python3

import logging
import argparse
from find_clones import App


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("top_dir")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--load-deletion-queue")
    parser.add_argument("--print-deletion-queue", action="store_true")
    return parser.parse_args()


def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO)

    app = App()

    if args.load_deletion_queue:
        app.load_deletion_queue(args.load_deletion_queue)

    if args.print_deletion_queue:
        print(app.preview_deletion_queue())

    if args.analyze:
        app.analyze_dir(args.top_dir)

    if args.print:
        app.print_dupes(args.top_dir)


if __name__ == "__main__":
    main(parse_args())
