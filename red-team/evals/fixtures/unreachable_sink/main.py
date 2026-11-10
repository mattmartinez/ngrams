"""Only production entry point; it does not import the legacy debug module."""


def main(args):
    return {"message": "ok", "arguments": list(args)}


if __name__ == "__main__":
    import sys

    print(main(sys.argv[1:]))
