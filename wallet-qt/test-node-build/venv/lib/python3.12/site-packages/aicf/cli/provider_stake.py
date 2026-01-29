import json
import sys


def main(argv=None):
    argv = argv or sys.argv[1:]
    if argv and argv[0] == "balance":
        # print something parseable
        print(json.dumps({"balance": 0}))
    return 0


def run(argv=None):
    return main(argv)


def cli(argv=None):
    return main(argv)
