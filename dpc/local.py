"""Outil de dépannage LOCAL : utilisé directement par une personne devant le PC en panne."""
import os
import subprocess
import sys
import time

from . import VERSION, actions, checks
from .system import IS_WIN, NO_WINDOW, build_command, child_env, incremental_decoder, is_admin, machine_info

if IS_WIN:
    os.system("")  # active les couleurs ANSI dans la console Windows 10/11
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")  # évite un plantage si la console ne sait pas afficher un caractère
    except Exception:
        pass

C = {"ok": "\033[92m", "info": "\033[94m", "warn": "\033[93m", "error": "\033[91m", "b": "\033[1m", "dim": "\033[2m", "0": "\033[0m"}
LABEL = {"ok": "  OK  ", "info": " INFO ", "warn": "ATTENTION", "error": "PROBLÈME"}
ORDER = {"error": 0, "warn": 1, "info": 2, "ok": 3}


def color(text, key):
    return "%s%s%s" % (C[key], text, C["0"])


def ask(prompt):
    try:
        return input(prompt).replace("﻿", "").strip()
    except EOFError:
        return "q"


def pause():
    ask(color("\nEntrée pour continuer...", "dim"))


def title(t):
    print("\n" + color("=" * 64, "b"))
    print(color("  " + t, "b"))
    print(color("=" * 64, "b"))


def confirm(q):
    return ask(q + " [o/N] : ").lower() in ("o", "oui", "y", "yes")


# ------------------------------------------------------------------ exécution
def run_live(cmd, shell):
    """Exécute une commande en affichant sa sortie en direct. Ctrl+C l'interrompt."""
    args, use_shell, enc = build_command(cmd, shell)
    kw = {} if IS_WIN else {"start_new_session": True}
    p = subprocess.Popen(args, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=child_env(), creationflags=NO_WINDOW, **kw)
    dec = incremental_decoder(enc)
    fd = p.stdout.fileno()
    try:
        while True:
            b = os.read(fd, 4096)
            if not b:
                break
            sys.stdout.write(dec.decode(b).replace("\x00", ""))
            sys.stdout.flush()
        return p.wait()
    except KeyboardInterrupt:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        else:
            import signal
            os.killpg(p.pid, signal.SIGKILL)
        print(color("\n(interrompu)", "warn"))
        return -1


def do_action(a):
    title(a["label"])
    if a["desc"]:
        print(a["desc"])
    if a["internet"]:
        print(color("Nécessite Internet.", "info"))
    if a["warning"]:
        print(color("Attention : " + a["warning"], "warn"))
    print(color("Commande : " + a["cmd"], "dim"))
    if not confirm("\nLancer cette réparation ?"):
        return
    print(color("-" * 64, "dim"))
    rc = run_live(a["cmd"], a["shell"])
    print(color("-" * 64, "dim"))
    print(color("Terminé." if rc == 0 else "Terminé avec le code %s." % rc, "ok" if rc == 0 else "warn"))
    pause()


# ------------------------------------------------------------------ diagnostic
LAST = {"results": None, "time": None}


def fixes_of(r):
    return [a for a in (actions.get(f) for f in r["fixes"]) if a]


def print_problem(r, max_lines=14):
    """Affiche une fiche complète : constat, explication, éléments relevés, réparations."""
    print("\n" + color("┌─ %s  %s" % (LABEL[r["status"]].strip(), r["title"]), r["status"]))
    print(color("│ ", r["status"]) + color(r["summary"], "b"))
    if r.get("advice"):
        print(color("│", r["status"]))
        print(color("│ ", r["status"]) + color("Explication et solution :", "b"))
        for a in r["advice"]:
            print(color("│ ", r["status"]) + "  • " + a)
    if r["details"]:
        lines = r["details"].splitlines()
        print(color("│", r["status"]))
        print(color("│ ", r["status"]) + color("Éléments relevés sur ce PC :", "b"))
        for l in lines[:max_lines]:
            print(color("│ ", r["status"]) + "  " + l)
        if len(lines) > max_lines:
            print(color("│ ", r["status"]) + color("  … %d ligne(s) de plus (tapez le numéro du test pour tout voir)"
                                                    % (len(lines) - max_lines), "dim"))
    fx = fixes_of(r)
    if fx:
        print(color("│", r["status"]))
        print(color("│ ", r["status"]) + color("Réparations proposées : ", "b") + ", ".join(a["label"] for a in fx))
    print(color("└" + "─" * 63, r["status"]))


def show_results(results, with_details=True):
    results = sorted(results, key=lambda r: ORDER.get(r["status"], 9))
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ORDER}
    problems = [r for r in results if r["status"] in ("error", "warn")]
    if with_details and problems:
        title("Détail des problèmes trouvés (%d)" % len(problems))
        for r in problems:
            print_problem(r)
    title("Résumé du diagnostic")
    print("  %s   %s   %s" % (color("%d problème(s)" % counts["error"], "error"),
                              color("%d à surveiller" % counts["warn"], "warn"),
                              color("%d OK" % counts["ok"], "ok")))
    print()
    for i, r in enumerate(results, 1):
        print("  %2d. %s  %s%s%s : %s" % (i, color(LABEL[r["status"]].ljust(9), r["status"]), C["b"], r["title"], C["0"], r["summary"]))
    if not problems:
        print(color("\n  Aucun problème détecté. Si le PC a quand même un souci, regardez les tests INFO "
                    "ou lancez une réparation dans le menu Réparations.", "ok"))
    return results


def progress(r, done, total):
    bar = int(30 * done / total)
    sys.stdout.write("\r  [%s%s] %2d/%d  %s %-40s" % ("#" * bar, "." * (30 - bar), done, total,
                                                     color(LABEL[r["status"]].strip()[:9].ljust(9), r["status"]), r["title"][:40]))
    sys.stdout.flush()


def diag_menu():
    title("Diagnostic en cours (20 à 60 s)...")
    t = time.time()
    results = checks.run_all(progress)
    print()
    LAST["results"], LAST["time"] = results, time.strftime("%d/%m/%Y %H:%M")
    print(color("Terminé en %.0f s." % (time.time() - t), "dim"))
    first = True
    while True:
        ordered = show_results(results, with_details=first)
        first = False
        print(color("\n  Que faire ?", "b"))
        print("   numéro  = voir la fiche complète d'un test")
        if any(r["fixes"] for r in ordered if r["status"] in ("error", "warn")):
            print("   r       = réparation guidée (toutes les réparations conseillées, une par une)")
        print("   p       = réafficher le détail des problèmes")
        print("   s       = enregistrer le rapport")
        print("   Entrée  = revenir au menu")
        c = ask("\nVotre choix : ").lower()
        if not c or c == "q":
            return
        if c == "r":
            guided_repair(ordered)
        elif c == "p":
            first = True
        elif c == "s":
            export_report()
        elif c.isdigit() and 1 <= int(c) <= len(ordered):
            detail(ordered[int(c) - 1])


def guided_repair(results):
    todo = []
    for r in results:
        if r["status"] in ("error", "warn"):
            for a in fixes_of(r):
                if a["id"] not in [x[0]["id"] for x in todo] and a["id"] not in ("reboot", "reboot_safe"):
                    todo.append((a, r["title"]))
    if not todo:
        print(color("Aucune réparation automatique à proposer.", "info"))
        pause()
        return
    if IS_WIN and todo[0][0]["id"] != "restore_point":
        todo.insert(0, (actions.get("restore_point"), "sécurité (à faire avant de réparer)"))
    title("Réparation guidée : %d étape(s)" % len(todo))
    for i, (a, why) in enumerate(todo, 1):
        print("  %d. %s  %s" % (i, a["label"], color("(%s)" % why, "dim")))
    print(color("\nChaque réparation vous sera présentée et demandera confirmation (Ctrl+C pour arrêter).", "dim"))
    if not confirm("Commencer ?"):
        return
    for i, (a, why) in enumerate(todo, 1):
        print(color("\nÉtape %d/%d - pour : %s" % (i, len(todo), why), "info"))
        try:
            do_action(a)
        except KeyboardInterrupt:
            print(color("\nRéparation guidée arrêtée.", "warn"))
            return
    title("Réparation guidée terminée")
    print("Conseil : redémarrez le PC, puis relancez un diagnostic pour vérifier que les problèmes ont disparu.")
    pause()


def detail(r):
    title("%s - %s" % (r["title"], LABEL[r["status"]].strip()))
    print_problem(r, max_lines=10 ** 6)
    fixes = fixes_of(r)
    if not fixes:
        pause()
        return
    print(color("\nRéparations proposées :", "b"))
    for i, a in enumerate(fixes, 1):
        print("  %d. %s" % (i, a["label"]))
    c = ask("\nNuméro de la réparation à lancer, Entrée pour revenir : ")
    if c.isdigit() and 1 <= int(c) <= len(fixes):
        do_action(fixes[int(c) - 1])


# ------------------------------------------------------------------ réparations
def repair_menu():
    cat = actions.catalog()
    cats = []
    for a in cat:
        if a["category"] not in cats:
            cats.append(a["category"])
    while True:
        title("Réparations")
        items = []
        for c in cats:
            print(color("\n  " + c, "b"))
            for a in cat:
                if a["category"] == c:
                    items.append(a)
                    print("   %2d. %s" % (len(items), a["label"]))
        c = ask("\nNuméro de la réparation, Entrée pour revenir : ")
        if not c or c.lower() == "q":
            return
        if c.isdigit() and 1 <= int(c) <= len(items):
            do_action(items[int(c) - 1])


# ------------------------------------------------------------------ rapport
def export_report():
    if not LAST["results"]:
        print(color("Lancez d'abord un diagnostic.", "warn"))
        pause()
        return
    info = machine_info()
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    folder = desk if os.path.isdir(desk) else os.path.expanduser("~")
    path = os.path.join(folder, "rapport-depannage-%s.txt" % time.strftime("%Y%m%d-%H%M"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("Rapport DépannagePC %s - %s\n" % (VERSION, LAST["time"]))
        f.write("PC : %s - %s\n\n" % (info["hostname"], info["os"]))
        for r in sorted(LAST["results"], key=lambda r: ORDER.get(r["status"], 9)):
            f.write("[%s] %s : %s\n" % (LABEL[r["status"]].strip(), r["title"], r["summary"]))
            for a in r.get("advice", []):
                f.write("  * %s\n" % a)
            fx = fixes_of(r)
            if fx and r["status"] in ("error", "warn"):
                f.write("  Réparations proposées : %s\n" % ", ".join(a["label"] for a in fx))
            if r["details"]:
                f.write("    " + r["details"].replace("\n", "\n    ") + "\n")
            f.write("\n")
    print(color("Rapport enregistré : %s" % path, "ok"))
    pause()


def main():
    info = machine_info()
    while True:
        title("DépannagePC %s - %s (%s)" % (VERSION, info["hostname"], info["os"]))
        if not info["admin"]:
            print(color("  /!\\ Pas en administrateur : la plupart des réparations échoueront.", "warn"))
        print("  1) Diagnostic complet")
        print("  2) Réparations")
        print("  3) Enregistrer le rapport du dernier diagnostic")
        print("  q) Quitter")
        c = ask("\nVotre choix : ").lower()
        try:
            if c == "1":
                diag_menu()
            elif c == "2":
                repair_menu()
            elif c == "3":
                export_report()
            elif c == "q":
                return
        except KeyboardInterrupt:
            print()
