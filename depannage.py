"""DépannagePC - outil de dépannage local (Windows et Linux), utilisé directement sur le PC en panne."""
import sys

from dpc.system import is_admin, relaunch_as_admin

if __name__ == "__main__":
    if not is_admin() and "--no-elevate" not in sys.argv:
        print("Demande des droits administrateur...")
        if relaunch_as_admin(["--no-elevate"]):
            sys.exit(0)
        print("Élévation impossible : mode limité.")
    from dpc import local
    local.main()
