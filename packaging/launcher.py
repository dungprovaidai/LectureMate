"""PyInstaller entry point that imports the app through its package."""

from lecturemate.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
