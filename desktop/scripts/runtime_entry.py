"""Frozen entry point for the private desktop runtime."""
from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()
    from sentinel_agent.desktop_service import main
    main()
