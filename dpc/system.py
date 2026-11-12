"""Utilitaires système communs : exécution de commandes, droits admin, réseau local."""
import codecs
import os
import platform
import random
import shutil
import socket
import subprocess
import sys

IS_WIN = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")

PS_PREFIX = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
             "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Continue';")

NO_WINDOW = 0x08000000 if IS_WIN else 0


def build_command(cmd, shell):
    """Retourne (args, use_shell, encodage) pour subprocess selon le shell voulu."""
    if IS_WIN:
        if shell == "cmd":
            return cmd, True, "oem"
        return (["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", PS_PREFIX + cmd], False, "utf-8")
    sh = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
    return [sh, "-c", cmd], False, "utf-8"


def child_env():
    env = os.environ.copy()
    if not IS_WIN:
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        env.setdefault("SYSTEMD_PAGER", "")
        env.setdefault("PAGER", "cat")
    return env


def decode(data, enc):
    try:
        text = data.decode(enc, errors="replace")
    except LookupError:
        text = data.decode("latin-1", errors="replace")
    return text.replace("\x00", "")


def incremental_decoder(enc):
    try:
        return codecs.getincrementaldecoder(enc)(errors="replace")
    except LookupError:
        return codecs.getincrementaldecoder("latin-1")(errors="replace")


def run(cmd, shell=None, timeout=60):
    """Exécute une commande et renvoie (code_retour, sortie texte)."""
    if shell is None:
        shell = "ps" if IS_WIN else "sh"
    args, use_shell, enc = build_command(cmd, shell)
    try:
        p = subprocess.run(args, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout, env=child_env(),
                           creationflags=NO_WINDOW)
        return p.returncode, decode(p.stdout, enc).strip()
    except subprocess.TimeoutExpired:
        return -1, "(délai dépassé après %ds)" % timeout
    except Exception as e:  # commande introuvable, etc.
        return -1, "(erreur : %s)" % e


def which(name):
    return shutil.which(name) is not None


# ---------------------------------------------------------------- droits admin
def is_admin():
    if IS_WIN:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def relaunch_as_admin(extra_args):
    """Relance le programme avec élévation. Renvoie True si une relance a été tentée."""
    if getattr(sys, "frozen", False):
        exe, base = sys.executable, []
    else:
        exe, base = sys.executable, [os.path.abspath(sys.argv[0])]
    args = base + extra_args
    if IS_WIN:
        import ctypes
        params = subprocess.list2cmdline(args)
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return r > 32
    if which("sudo") and sys.stdin and sys.stdin.isatty():
        os.execvp("sudo", ["sudo", exe] + args)
    return False


# ---------------------------------------------------------------- infos machine
def os_name():
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            name = winreg.QueryValueEx(k, "ProductName")[0]
            build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
            try:
                disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
            except OSError:
                disp = ""
            if build >= 22000:
                name = name.replace("Windows 10", "Windows 11")
            return ("%s %s (build %d)" % (name, disp, build)).replace("  ", " ")
        except Exception:
            return platform.platform()
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def machine_info():
    return {
        "hostname": socket.gethostname(),
        "os": os_name(),
        "family": "windows" if IS_WIN else "linux",
        "admin": is_admin(),
        "user": os.environ.get("USERNAME") or os.environ.get("SUDO_USER") or os.environ.get("USER") or "?",
        "kernel": platform.release(),
        "arch": platform.machine(),
    }


# ---------------------------------------------------------------- réseau local
def local_ipv4():
    """Liste de (ip, prefixe) des interfaces IPv4 non loopback."""
    res = []
    if not IS_WIN and which("ip"):
        rc, out = run("ip -4 -o addr show", shell="sh", timeout=5)
        for line in out.splitlines():
            parts = line.split()
            if "inet" in parts:
                addr = parts[parts.index("inet") + 1]
                ip, _, pre = addr.partition("/")
                if not ip.startswith("127."):
                    res.append((ip, int(pre or 24)))
    if not res:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and all(ip != r[0] for r in res):
                    res.append((ip, 16 if ip.startswith("169.254.") else 24))
        except OSError:
            pass
    return res


def broadcast_addr(ip, prefix):
    n = int.from_bytes(socket.inet_aton(ip), "big")
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return socket.inet_ntoa(((n & mask) | (~mask & 0xFFFFFFFF)).to_bytes(4, "big"))


def ensure_link_local(log=print):
    """Linux : si une interface filaire est branchée mais sans IPv4 (câble direct, pas de DHCP),
    lui attribue une adresse 169.254.x.y comme le fait Windows automatiquement."""
    if not IS_LINUX or not is_admin() or not which("ip"):
        return
    base = "/sys/class/net"
    try:
        ifaces = os.listdir(base)
    except OSError:
        return
    for ifc in ifaces:
        if ifc == "lo" or os.path.exists(os.path.join(base, ifc, "wireless")):
            continue
        if not os.path.exists(os.path.join(base, ifc, "device")):
            continue  # interface virtuelle (docker, bridge...)
        try:
            with open(os.path.join(base, ifc, "carrier")) as f:
                if f.read().strip() != "1":
                    continue
        except OSError:
            continue
        rc, out = run("ip -4 -o addr show dev %s" % ifc, shell="sh", timeout=5)
        if "inet " in out:
            continue
        ip = "169.254.%d.%d" % (random.randint(1, 254), random.randint(1, 254))
        rc, out = run("ip addr add %s/16 dev %s && ip link set %s up" % (ip, ifc, ifc), shell="sh", timeout=5)
        if rc == 0:
            log("  Câble direct détecté sur %s : adresse %s attribuée." % (ifc, ip))
