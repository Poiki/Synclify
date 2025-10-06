from __future__ import annotations

from synclify.cli import main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
