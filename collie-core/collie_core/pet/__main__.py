"""Entry point for the Collie Desktop Pet.

Usage: python -m collie_core.pet
"""

import sys

from collie_core.pet.collie_pet import ColliePet


def main() -> None:
    try:
        pet = ColliePet()
        pet.run()
    except ImportError as e:
        print(f"Missing dependency: {e}", file=sys.stderr)
        print("Install with: pip install Pillow", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Collie pet closed.")
    except Exception as e:
        print(f"Pet error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
