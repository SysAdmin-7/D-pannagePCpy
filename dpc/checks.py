"""Diagnostic automatique : chaque test renvoie un résultat avec état et réparations suggérées."""
import concurrent.futures
import json
import os
import re
import shutil
import socket
import time

from .actions import CRITICAL_WIN_SERVICES
from .system import IS_WIN, is_admin, machine_info, run, which

OK, INFO, WARN, ERROR = "ok", "info", "warn", "error"


def R(id, title, status, summary, details="", fixes=None, advice=None):
    """advice : liste de phrases simples (cause probable, conséquence, que faire)."""
    return {"id": id, "title": title, "status": status, "summary": summary,
            "details": details, "fixes": fixes or [], "advice": advice or []}


def ps_json(cmd, timeout=60):
    rc, out = run(cmd + " | ConvertTo-Json -Depth 3 -Compress", shell="ps", timeout=timeout)
    try:
        data = json.loads(out) if out else []
    except ValueError:
        return None, out
    if isinstance(data, dict):
        data = [data]
    return data, out


def human(n):
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f Po" % n


# ======================================================================= tests
def check_system():
    info = machine_info()
    if IS_WIN:
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        up = ctypes.windll.kernel32.GetTickCount64() / 1000.0
    else:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    days = up / 86400
    details = "\n".join("%s : %s" % (k, v) for k, v in info.items())
    details += "\nAllumé depuis : " + ("%.1f jours" % days if days >= 1 else "%d h %02d min" % (up // 3600, up % 3600 // 60))
    if not info["admin"]:
        return R("system", "Système", WARN, "DépannagePC n'est pas lancé en administrateur : réparations limitées.", details,
                 advice=["Sans droits administrateur, Windows/Linux refuse la plupart des réparations (DNS, SFC, services…).",
                         "Windows : fermez l'outil, clic droit sur DepannagePC > « Exécuter en tant qu'administrateur ».",
                         "Linux : relancez avec « sudo ./lancer.sh »."])
    if days > 14:
        return R("system", "Système", WARN, "%s - allumé depuis %d jours sans redémarrage" % (info["os"], days),
                 details, ["reboot"],
                 ["Un PC jamais redémarré accumule fuites de mémoire et mises à jour en attente : lenteurs et bugs bizarres.",
                  "Sous Windows, « Arrêter » ne remet pas tout à zéro (démarrage rapide) : seul « Redémarrer » le fait.",
                  "Que faire : redémarrer le PC, puis refaire un diagnostic."])
    return R("system", "Système", INFO, "%s - %s" % (info["os"], details.splitlines()[-1].lower()), details)


def check_memory():
    if IS_WIN:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ave", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        total, avail = m.total, m.avail
    else:
        mi = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mi[k] = int(v.split()[0]) * 1024
        total, avail = mi["MemTotal"], mi.get("MemAvailable", mi.get("MemFree", 0))
    used = 100.0 * (total - avail) / total
    s = "%s utilisés sur %s (%.0f %%)" % (human(total - avail), human(total), used)
    status = ERROR if used > 95 else WARN if used > 85 else OK
    advice = []
    if used > 85:
        advice += ["La mémoire est presque pleine : le PC utilise alors le disque à la place, ce qui le rend très lent.",
                   "Cause fréquente : navigateur avec beaucoup d'onglets, logiciel qui fuit, ou malware.",
                   "Que faire : regardez « Processus les plus gourmands » ci-dessous et fermez le programme en tête ; "
                   "s'il est inconnu, lancez une analyse antivirus."]
    if total < 4 * 1024 ** 3:
        s += " - moins de 4 Go de RAM : lenteurs probables"
        status = WARN if status == OK else status
        advice.append("Moins de 4 Go de RAM est insuffisant pour un système récent : ajouter une barrette est la vraie solution.")
    return R("memory", "Mémoire vive", status, s, "Voir « Processus les plus gourmands » pour les plus gros consommateurs.",
             ["defender_scan"] if (IS_WIN and used > 85) else [], advice)


def _mounts():
    if IS_WIN:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        res = []
        for i in range(26):
            if mask & (1 << i):
                root = "%s:\\" % chr(65 + i)
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # disque fixe
                    res.append((root, root, ""))
        return res
    real = {"ext2", "ext3", "ext4", "xfs", "btrfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "f2fs", "zfs", "jfs", "reiserfs"}
    res, seen = [], set()
    with open("/proc/mounts") as f:
        for line in f:
            dev, mnt, fs, opts = line.split()[:4]
            mnt = mnt.replace("\\040", " ")
            if fs in real and dev not in seen and not mnt.startswith(("/snap", "/boot/efi")):
                seen.add(dev)
                res.append((dev, mnt, opts))
    return res


def check_disks():
    lines, status, fixes = [], OK, []
    for dev, mnt, opts in _mounts():
        try:
            u = shutil.disk_usage(mnt)
        except OSError:
            continue
        free = 100.0 * u.free / u.total if u.total else 100
        st = ERROR if free < 3 or u.free < 1024 ** 3 else WARN if free < 10 else OK
        line = "%s  %s libres sur %s (%.0f %% libre)" % (mnt, human(u.free), human(u.total), free)
        if not IS_WIN:
            if mnt == "/" and "ro" in opts.split(","):
                st = ERROR
                line += "  <-- MONTÉ EN LECTURE SEULE (erreur disque probable)"
                fixes.append("remount_rw")
            try:
                sv = os.statvfs(mnt)
                if sv.f_files and 100.0 * sv.f_favail / sv.f_files < 5:
                    st = ERROR
                    line += "  <-- plus d'inodes libres"
            except OSError:
                pass
        if st != OK:
            line = "[!] " + line
        lines.append(line)
        status = max(status, st, key=[OK, INFO, WARN, ERROR].index)
    if status != OK:
        fixes = fixes + (["clean_temp", "empty_recycle", "clean_update_cache"] if IS_WIN else ["clean_space"])
    summ = {OK: "Espace disque suffisant", WARN: "Espace disque faible", ERROR: "Disque plein ou en erreur"}[status]
    advice = []
    if status != OK:
        advice = ["Un disque presque plein empêche les mises à jour, ralentit fortement le PC et peut faire planter des logiciels.",
                  "Il faut garder au moins 10 % d'espace libre sur le disque système.",
                  "Que faire : lancer le nettoyage ci-dessous, puis déplacer photos/vidéos vers un disque externe "
                  "et désinstaller les jeux/logiciels inutilisés."]
        if "remount_rw" in fixes:
            advice.insert(0, "Le système s'est mis en LECTURE SEULE : Linux le fait quand il détecte des erreurs disque. "
                             "Sauvegardez vos données avant toute autre chose.")
    return R("disks", "Espace disque", status, summ, "\n".join(lines) or "Aucun disque détecté", fixes, advice)


def check_disk_health():
    if IS_WIN:
        data, raw = ps_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,"
                            "@{n='SizeGB';e={[int]($_.Size/1GB)}}")
        if data is None:
            return R("smart", "Santé des disques", INFO, "Impossible de lire l'état des disques", raw)
        bad = [d for d in data if str(d.get("HealthStatus")) not in ("Healthy", "0")]
        det = "\n".join("%s (%s, %s Go) : santé=%s, état=%s" % (d.get("FriendlyName"), d.get("MediaType"),
                        d.get("SizeGB"), d.get("HealthStatus"), d.get("OperationalStatus")) for d in data)
        rc, dirty = run("fsutil dirty query %SystemDrive%", shell="cmd", timeout=10)
        det += "\n" + dirty
        if bad:
            return R("smart", "Santé des disques", ERROR,
                     "Disque en mauvaise santé : SAUVEGARDEZ LES DONNÉES et remplacez-le", det, ["disk_health", "chkdsk"],
                     ["Le disque lui-même signale qu'il est en train de lâcher (secteurs défectueux, usure du SSD).",
                      "Aucun logiciel ne peut réparer un disque physiquement abîmé : il peut mourir à tout moment.",
                      "Que faire, dans cet ordre : 1) copier immédiatement les documents importants sur une clé/disque externe ; "
                      "2) remplacer le disque ; 3) réinstaller ou cloner le système."])
        dl = dirty.lower()
        negated = any(w in dl for w in ("not dirty", "is not", "n'est pas", "erreur", "error", "accès", "access"))
        if not negated and ("dirty" in dl or "incohérent" in dl):
            return R("smart", "Santé des disques", WARN, "Le système de fichiers est marqué comme à vérifier", det, ["chkdsk"],
                     ["Windows a détecté une incohérence (souvent après une coupure de courant ou un arrêt forcé).",
                      "Que faire : programmer CHKDSK puis redémarrer ; la vérification se fait avant le démarrage de Windows."])
        return R("smart", "Santé des disques", OK, "Tous les disques sont en bonne santé (%d)" % len(data), det, ["disk_health"])
    rc, io = run("journalctl -k -b --no-pager -q 2>/dev/null | grep -iE 'I/O error|blk_update_request|ata[0-9.]+: (exception|failed)|medium error|EXT4-fs error|BTRFS error' | tail -n 15")
    det = ""
    status = OK
    if which("smartctl"):
        rc, out = run("for d in $(smartctl --scan | awk '{print $1}'); do echo \"$d: $(smartctl -H $d | grep -iE 'result|status')\"; done")
        det = out
        if "FAIL" in out.upper():
            status = ERROR
    else:
        det = "smartctl non installé (paquet smartmontools) : état SMART indisponible.\n"
        status = INFO
    if io.strip():
        det += "\n\nErreurs disque dans les journaux du noyau :\n" + io
        status = ERROR
    summ = {OK: "Disques en bonne santé", INFO: "Pas d'erreur disque dans les journaux", ERROR: "Erreurs disque détectées : SAUVEGARDEZ LES DONNÉES"}[status]
    advice = []
    if status == ERROR:
        advice = ["Le noyau a enregistré des erreurs de lecture/écriture : le disque ou son câble est probablement défaillant.",
                  "Que faire : sauvegarder immédiatement les données, vérifier le câble SATA, puis remplacer le disque si les erreurs continuent."]
    return R("smart", "Santé des disques", status, summ, det,
             ["disk_health", "fsck_boot"] if status == ERROR else ["disk_health"], advice)


def check_cpu():
    cpu_advice = ["Le processeur est saturé : tout le PC rame, chauffe et le ventilateur tourne fort.",
                  "Les programmes en tête de liste ci-dessus sont les responsables.",
                  "Que faire : fermer le programme fautif ; si c'est un nom inconnu ou bizarre (ex. mineur de cryptomonnaie), "
                  "lancer une analyse antivirus. Si « Windows Update » / « TiWorker » est en tête, laissez-le finir."]
    if IS_WIN:
        n = os.cpu_count() or 1
        # 3 mesures espacées pour éviter un pic ponctuel
        rc, out = run("$s=@(); 1..3 | ForEach-Object { $s += (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average; Start-Sleep -Milliseconds 700 };"
                      "Write-Output ('LOAD=' + [int](($s | Measure-Object -Average).Average));"
                      "Write-Output ('NAME=' + (Get-CimInstance Win32_Processor | Select-Object -First 1).Name);"
                      "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -notin '_Total','Idle' } | "
                      "Sort-Object PercentProcessorTime -Descending | Select-Object -First 6 | ForEach-Object { 'TOP=' + $_.Name + '|' + $_.PercentProcessorTime }",
                      timeout=60)
        load, name, top = 0, "?", []
        for line in out.splitlines():
            if line.startswith("LOAD="):
                load = int(line[5:] or 0) if line[5:].strip().isdigit() else 0
            elif line.startswith("NAME="):
                name = line[5:].strip()
            elif line.startswith("TOP="):
                pname, _, pct = line[4:].partition("|")
                try:
                    top.append((pname, float(pct) / n))
                except ValueError:
                    pass
        det = "Charge moyenne (3 mesures) : %d %%\n\nProgrammes qui utilisent le plus le processeur :\n" % load
        det += "\n".join("  %-35s %5.1f %%" % t for t in top if t[1] >= 0.5) or "  (aucun notable)"
        st = ERROR if load > 95 else WARN if load > 80 else OK
        summ = "%s - charge %d %%" % (name, load)
        if st != OK and top:
            summ += " (surtout : %s)" % top[0][0]
        return R("cpu", "Processeur", st, summ, det, ["defender_scan"] if st != OK else [], cpu_advice if st != OK else [])
    name = "?"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    l1, l5, l15 = os.getloadavg()
    n = os.cpu_count() or 1
    det = "Charge moyenne : %.2f / %.2f / %.2f (%d cœurs)" % (l1, l5, l15, n)
    temps = []
    base = "/sys/class/thermal"
    if os.path.isdir(base):
        for z in sorted(os.listdir(base)):
            try:
                with open(os.path.join(base, z, "temp")) as f:
                    t = int(f.read()) / 1000.0
                with open(os.path.join(base, z, "type")) as f:
                    temps.append((f.read().strip(), t))
            except (OSError, ValueError):
                pass
    st = WARN if l5 > n * 1.5 else OK
    advice = cpu_advice if st != OK else []
    rc, top = run("ps -eo pid,comm,%cpu --sort=-%cpu | head -n 7", timeout=10)
    det += "\n\nProgrammes qui utilisent le plus le processeur :\n" + top
    if temps:
        det += "\n\nTempératures : " + ", ".join("%s %.0f°C" % t for t in temps)
        if max(t for _, t in temps) > 90:
            st = ERROR
            advice = ["Le processeur dépasse 90 °C : il ralentit pour se protéger, et le PC peut s'éteindre tout seul.",
                      "Cause habituelle : poussière dans le ventilateur/radiateur ou pâte thermique sèche.",
                      "Que faire : souffler la poussière (bombe d'air sec, PC éteint), ne pas poser le portable sur un lit/canapé, "
                      "refaire la pâte thermique si le PC a plusieurs années."]
    summ = "%s - charge %.1f pour %d cœurs" % (name, l5, n)
    return R("cpu", "Processeur", st, summ + (" - SURCHAUFFE" if st == ERROR else ""), det, [], advice)


def check_processes():
    if IS_WIN:
        rc, out = run("Get-Process | Sort-Object WS -Descending | Select-Object -First 10 Name,Id,"
                      "@{n='RAM(Mo)';e={[int]($_.WS/1MB)}},@{n='CPU(s)';e={[int]$_.CPU}} | Format-Table -AutoSize | Out-String -Width 160")
    else:
        rc, out = run("ps -eo pid,user,comm,%cpu,%mem --sort=-%mem | head -n 11")
    return R("procs", "Processus les plus gourmands", INFO, "Top 10 par mémoire utilisée", out)


def _tcp_ok(host, port, timeout=3):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _dns_ok(name="www.google.com", timeout=5):
    ex = concurrent.futures.ThreadPoolExecutor(1)
    try:
        return bool(ex.submit(socket.gethostbyname, name).result(timeout=timeout))
    except Exception:
        return False
    finally:
        ex.shutdown(wait=False)


def check_network():
    if IS_WIN:
        rc, gw = run("(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop", timeout=20)
        rc, det = run("ipconfig", shell="cmd", timeout=15)
        ping = "ping -n 2 -w 1000 %s"
        fixes = ["flush_dns", "renew_ip", "reset_proxy", "reset_network", "set_dns_public"]
    else:
        rc, gw = run("ip route show default | awk '{print $3; exit}'", timeout=5)
        rc, det = run("ip -br addr; echo; ip route; echo; cat /etc/resolv.conf 2>/dev/null | grep -v '^#'", timeout=5)
        ping = "ping -c 2 -W 1 %s"
        fixes = ["flush_dns", "restart_network", "set_dns_public"]
    gw = gw.strip().splitlines()[0] if gw.strip() else ""
    if gw in ("0.0.0.0", ""):
        return R("network", "Réseau / Internet", WARN,
                 "Aucune connexion au réseau : le PC n'est relié à aucune box/routeur",
                 det, fixes[:2],
                 ["Le PC n'a reçu aucune adresse de la box : il n'est connecté à rien.",
                  "Causes possibles : Wi-Fi désactivé (touche avion, Fn+F2…), câble débranché, mauvais mot de passe Wi-Fi, "
                  "box éteinte, ou carte réseau sans pilote (voir « Périphériques / pilotes »).",
                  "Que faire : vérifier le Wi-Fi/le câble, redémarrer la box, puis « Renouveler l'adresse IP »."])
    gw_ok = run(ping % gw, shell=("cmd" if IS_WIN else "sh"), timeout=10)[0] == 0
    net_ok = _tcp_ok("1.1.1.1", 53) or _tcp_ok("8.8.8.8", 53)
    dns_ok = _dns_ok()
    web_ok = _tcp_ok("www.google.com", 443) if dns_ok else False
    det = "Passerelle %s : %s\nInternet (IP) : %s\nDNS : %s\nWeb (HTTPS) : %s\n\n%s" % (
        gw, "OK" if gw_ok else "ne répond pas", "OK" if net_ok else "ÉCHEC",
        "OK" if dns_ok else "ÉCHEC", "OK" if web_ok else "ÉCHEC", det)
    if net_ok and dns_ok and web_ok:
        return R("network", "Réseau / Internet", OK, "Connexion Internet fonctionnelle", det)
    if net_ok and not dns_ok:
        return R("network", "Réseau / Internet", ERROR, "Internet OK mais les noms de sites ne se résolvent pas (DNS)", det,
                 ["flush_dns", "set_dns_public"] + fixes,
                 ["La connexion fonctionne, mais le « carnet d'adresses » d'Internet (DNS) ne répond pas : "
                  "les sites ne s'ouvrent pas alors que Discord/jeux peuvent marcher.",
                  "Que faire : « Vider le cache DNS », et si ça ne suffit pas, « Utiliser des DNS publics »."])
    if not gw_ok and not net_ok:
        return R("network", "Réseau / Internet", ERROR, "La box/routeur (%s) ne répond pas" % gw, det, fixes,
                 ["Le PC est connecté mais la box ne répond pas.",
                  "Que faire : redémarrer la box (débrancher 30 s), se rapprocher en Wi-Fi ou essayer un câble, "
                  "puis « Renouveler l'adresse IP ». Si les autres appareils marchent, « Réinitialiser la pile réseau »."])
    if not net_ok:
        return R("network", "Réseau / Internet", ERROR, "Réseau local OK mais pas d'accès Internet", det, fixes,
                 ["La box répond mais Internet est inaccessible.",
                  "Si les autres appareils (téléphone en Wi-Fi) ont Internet : le problème vient du PC "
                  "(proxy, antivirus/VPN, pile réseau) -> « Supprimer le proxy », puis « Réinitialiser la pile réseau ».",
                  "Si aucun appareil n'a Internet : panne de la box ou du fournisseur d'accès."])
    return R("network", "Réseau / Internet", WARN, "Internet partiellement accessible (les sites web ne répondent pas)", det, fixes,
             ["Le PC atteint Internet mais pas les sites web (HTTPS).",
              "Causes fréquentes : proxy installé par un logiciel, antivirus/pare-feu tiers, VPN, ou date du PC fausse.",
              "Que faire : « Supprimer le proxy système », vérifier la date (test « Date et heure »), désactiver un VPN."])


def check_services():
    if IS_WIN:
        data, raw = ps_json("Get-Service -Name %s -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Running' -and $_.StartType -ne 'Disabled' -and $_.StartType -ne 'Manual' } | Select-Object Name,DisplayName,@{n='Status';e={[string]$_.Status}}"
                            % ",".join("'%s'" % s for s in CRITICAL_WIN_SERVICES))
        if data is None:
            return R("services", "Services essentiels", INFO, "Lecture impossible", raw)
        if not data:
            return R("services", "Services essentiels", OK, "Tous les services essentiels tournent")
        det = "\n".join("%s (%s) : %s" % (d["DisplayName"], d["Name"], d["Status"]) for d in data)
        return R("services", "Services essentiels", WARN, "%d service(s) essentiel(s) arrêté(s)" % len(data), det, ["start_services"],
                 ["Ces services Windows devraient toujours tourner. Selon le service arrêté : pas de son (Audiosrv), "
                  "pas de réseau (Dhcp, Dnscache, NlaSvc), pas d'impression (Spooler), pare-feu coupé (mpssvc)…",
                  "Que faire : « Redémarrer les services essentiels arrêtés ». S'ils s'arrêtent à nouveau, "
                  "un logiciel ou un malware les coupe : lancer une analyse antivirus et « Réparer les fichiers système »."])
    if not which("systemctl"):
        return R("services", "Services", INFO, "systemd absent : test ignoré")
    rc, out = run("systemctl --failed --no-legend --plain", timeout=15)
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return R("services", "Services", OK, "Aucun service en échec")
    return R("services", "Services", WARN, "%d service(s) en échec" % len(lines), out, ["restart_failed"],
             ["Ces services système n'ont pas réussi à démarrer.",
              "Que faire : les relancer ; s'ils échouent encore, la cause s'affiche dans la sortie de la réparation "
              "(fichier de configuration erroné, paquet cassé, disque plein…)."])


# Erreurs Windows connues : (source, id ou None) -> (gravité, explication, réparations)
# gravité : 0 = sans importance, 1 = à surveiller, 2 = grave
WIN_EVENTS = [
    ("Microsoft-Windows-DistributedCOM", None, 0, "Avertissement de permissions DCOM : présent sur tous les PC, sans gravité.", []),
    ("Microsoft-Windows-WHEA-Logger", None, 2, "ERREUR MATÉRIELLE signalée par le processeur, la RAM ou un port PCIe "
     "(surchauffe, overclocking, RAM défaillante).", []),
    ("BugCheck", None, 2, "Écran bleu (plantage complet de Windows).", ["sfc", "dism"]),
    ("Microsoft-Windows-Kernel-Power", 41, 2, "Arrêt brutal du PC (coupure de courant, bouton maintenu, plantage).", ["disable_fastboot"]),
    ("disk", None, 2, "Erreur de lecture/écriture du DISQUE : il est peut-être en train de lâcher, sauvegardez.", ["disk_health", "chkdsk"]),
    ("Ntfs", None, 2, "Système de fichiers endommagé.", ["chkdsk"]),
    ("Microsoft-Windows-Ntfs", None, 2, "Système de fichiers endommagé.", ["chkdsk"]),
    ("volmgr", None, 1, "Problème de configuration du fichier de vidage mémoire (souvent sans conséquence).", []),
    ("Application Error", 1000, 1, "Un logiciel a planté.", ["sfc"]),
    ("Application Hang", 1002, 1, "Un logiciel a cessé de répondre (figé).", []),
    ("Windows Error Reporting", None, 0, "Rapport d'erreur envoyé à Microsoft (suite d'un plantage de logiciel).", []),
    ("Service Control Manager", None, 1, "Un service Windows n'a pas démarré ou s'est arrêté de façon inattendue.", ["start_services"]),
    ("Microsoft-Windows-WindowsUpdateClient", None, 1, "Échec d'installation d'une mise à jour Windows.", ["reset_wu"]),
    ("Microsoft-Windows-DNS-Client", None, 1, "Échec de résolution DNS (sites inaccessibles).", ["flush_dns"]),
    ("Microsoft-Windows-Kernel-PnP", None, 1, "Un pilote de périphérique n'a pas pu se charger.", ["rescan_devices"]),
    ("Display", None, 1, "Le pilote graphique a planté puis redémarré (écran noir quelques secondes).", []),
    ("nvlddmkm", None, 1, "Le pilote graphique NVIDIA a planté (écran noir / jeu qui se ferme) : mettez-le à jour.", []),
    ("amdkmdag", None, 1, "Le pilote graphique AMD a planté : mettez-le à jour.", []),
    ("Microsoft-Windows-Security-SPP", None, 0, "Service de licence Windows (généralement sans gravité).", []),
    ("SideBySide", None, 0, "Composant Visual C++ manquant pour un logiciel (réinstaller le logiciel concerné).", []),
    ("ESENT", None, 0, "Base de données interne de Windows (indexation), généralement sans gravité.", []),
    ("Microsoft-Windows-Time-Service", None, 0, "Synchronisation de l'heure impossible (souvent sans Internet).", ["time_sync"]),
    ("Microsoft-Windows-Hyper-V-Hypervisor", None, 0, "Virtualisation, sans gravité.", []),
    ("IntelHaxm", None, 0, "Accélérateur d'émulateur Android (Intel HAXM) inutilisable : sans gravité sauf si vous utilisez un émulateur.", []),
    ("Killer Analytics Service", None, 0, "Service de statistiques de la carte réseau Killer, sans gravité.", []),
    (".NET Runtime", 1022, 0, "Message technique .NET (profileur), sans gravité.", []),
    ("Schannel", None, 0, "Échec d'une connexion sécurisée (TLS) vers un serveur : courant et généralement sans gravité.", []),
    ("Microsoft-Windows-Windows Firewall With Advanced Security", 2042, 0, "Lecture de configuration du pare-feu refusée, sans gravité.", []),
    ("Microsoft-Windows-Perflib", None, 0, "Compteur de performances d'un logiciel absent, sans gravité.", []),
    ("Microsoft-Windows-AppModel-Runtime", None, 0, "Application du Store, généralement sans gravité.", []),
    ("Microsoft-Windows-User Profiles Service", None, 1, "Problème de chargement du profil utilisateur (session temporaire possible).", []),
    ("Microsoft-Windows-Kernel-Boot", None, 1, "Avertissement au démarrage de Windows.", []),
]

# Services dont l'échec de démarrage est courant et sans gravité (logiciels de mise à jour, etc.)
BENIGN_SERVICES = ("gupdate", "edgeupdate", "Google Update", "Microsoft Edge Update", "XboxService", "XblGameSave",
                   "MapsBroker", "sppsvc", "RemoteRegistry", "Steam Client Service", "Brave", "AdobeARMservice")

APP_RE = re.compile(r"(?:application name|l.application défaillante|application défaillante)\s*:?\s*([^,\s]+)", re.I)


def _classify_win(provider, eid, msg=""):
    if provider == "Service Control Manager" and any(s.lower() in msg.lower() for s in BENIGN_SERVICES):
        return 0, "Service de mise à jour / service secondaire qui n'a pas démarré : sans gravité.", []
    for p, i, sev, text, fixes in WIN_EVENTS:
        if provider == p and (i is None or i == eid):
            return sev, text, fixes
    return 1, "", []


def check_logs():
    if not IS_WIN:
        rc, out = run("journalctl -p 3 -b --no-pager -q -o short -n 200 2>/dev/null", timeout=30)
        lines = [l for l in out.splitlines() if l.strip()]
        groups = {}
        for l in lines:
            parts = l.split(None, 4)
            src = parts[3].split("[")[0].rstrip(":") if len(parts) > 4 else "?"
            groups.setdefault(src, []).append(parts[4] if len(parts) > 4 else l)
        det = "\n".join("%3d x %s\n      ex : %s" % (len(v), k, v[-1][:180])
                        for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])))
        n = len(lines)
        st = OK if n == 0 else INFO if n <= 20 else WARN
        return R("logs", "Journaux d'erreurs récents", st, "%d erreur(s) depuis le démarrage, %d source(s)" % (n, len(groups)),
                 det, [], ["Erreurs enregistrées par le système depuis le démarrage, regroupées par programme.",
                           "Quelques erreurs sont normales ; une même source qui se répète beaucoup indique le composant à réparer."]
                 if n else [])
    data, raw = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;StartTime=(Get-Date).AddDays(-3)} "
                        "-MaxEvents 500 -ErrorAction SilentlyContinue | Select-Object @{n='P';e={$_.ProviderName}},@{n='I';e={$_.Id}},"
                        "@{n='T';e={$_.TimeCreated.ToString('dd/MM HH:mm')}},@{n='M';e={(($_.Message -replace '\\s+',' ') -replace '^(.{0,240}).*','$1')}}",
                        timeout=90)
    if data is None:
        return R("logs", "Journaux d'erreurs récents", INFO, "Lecture des journaux impossible", raw)
    if not data:
        return R("logs", "Journaux d'erreurs récents", OK, "Aucune erreur dans les journaux depuis 3 jours")
    groups = {}
    for e in data:
        key = (e.get("P") or "?", e.get("I"))
        g = groups.setdefault(key, {"n": 0, "last": e.get("T"), "msg": e.get("M") or "", "apps": set()})
        g["n"] += 1
        m = APP_RE.search(e.get("M") or "")
        if m:
            g["apps"].add(m.group(1))
    serious, medium, benign, fixes = [], [], 0, []
    lines = []
    for (prov, eid), g in sorted(groups.items(), key=lambda kv: (-_classify_win(kv[0][0], kv[0][1], kv[1]["msg"])[0], -kv[1]["n"])):
        sev, text, fx = _classify_win(prov, eid, g["msg"])
        tag = ["sans gravité", "à surveiller", "GRAVE"][sev]
        line = "[%s] %d x %s (code %s) - dernière : %s" % (tag, g["n"], prov, eid, g["last"])
        if text:
            line += "\n      -> " + text
        if g["apps"]:
            line += "\n      logiciel(s) concerné(s) : " + ", ".join(sorted(g["apps"])[:6])
        if g["msg"] and sev > 0:
            line += "\n      message : " + g["msg"][:200]
        lines.append(line)
        if sev == 2:
            serious.append(text or prov)
        elif sev == 1:
            medium.append(prov)
        else:
            benign += g["n"]
        if sev > 0:
            fixes += [f for f in fx if f not in fixes]
    total = len(data)
    if serious:
        st, summ = ERROR, "%d erreur(s) en 3 jours dont des erreurs GRAVES : %s" % (total, serious[0].split(" (")[0].split(":")[0])
    elif len(medium) > 0 and total - benign > 15:
        st, summ = WARN, "%d erreur(s) en 3 jours (%d sans gravité)" % (total, benign)
    else:
        st, summ = INFO, "%d erreur(s) en 3 jours, surtout sans gravité" % total
    adv = ["Windows note chaque erreur dans ses journaux. Tous les PC en ont : ce qui compte, c'est leur type.",
           "Ci-dessus, chaque type d'erreur est classé (GRAVE / à surveiller / sans gravité) avec son explication."]
    if serious:
        adv.append("Erreurs GRAVES trouvées : traitez-les en priorité, avec les réparations proposées.")
    return R("logs", "Journaux d'erreurs récents", st, summ, "\n".join(lines), fixes, adv)


def check_crashes():
    if IS_WIN:
        rc, out = run("$d=(Get-Date).AddDays(-30);"
                      "$kp=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41;StartTime=$d} -ErrorAction SilentlyContinue).Count;"
                      "$bc=@(Get-WinEvent -FilterHashtable @{LogName='System';Id=1001;StartTime=$d} -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'BugCheck|WER-SystemErrorReporting' });"
                      "$md=@(Get-ChildItem \"$env:SystemRoot\\Minidump\" -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt $d);"
                      "Write-Output \"KP=$kp\";Write-Output \"BSOD=$($bc.Count)\";Write-Output \"DUMP=$($md.Count)\";"
                      "$bc | Select-Object -First 5 | ForEach-Object { $_.TimeCreated.ToString('dd/MM HH:mm') + ' : ' + (($_.Message -replace '\\s+',' ') -replace '^(.{0,250}).*','$1') }",
                      timeout=60)
        vals = {}
        for line in out.splitlines():
            if "=" in line and line.split("=")[0] in ("KP", "BSOD", "DUMP"):
                k, v = line.split("=", 1)
                vals[k] = int(v or 0) if v.strip().isdigit() else 0
        kp, bsod, dump = vals.get("KP", 0), vals.get("BSOD", 0), vals.get("DUMP", 0)
        det = ("Sur 30 jours :\n- Arrêts brutaux (coupure/plantage, Kernel-Power 41) : %d\n- Écrans bleus (BSOD) : %d\n"
               "- Fichiers minidump : %d\n\n%s\n\nPistes : écrans bleus répétés => pilotes, RAM (lancer mdsched.exe), disque. "
               "Arrêts brutaux sans BSOD => alimentation, surchauffe, bouton." % (kp, bsod, dump,
                "\n".join(l for l in out.splitlines() if not l.startswith(("KP=", "BSOD=", "DUMP=")))))
        if bsod or dump:
            return R("crashes", "Plantages", ERROR, "%d écran(s) bleu(s) ce mois-ci" % max(bsod, dump), det,
                     ["restore_point", "sfc", "dism", "chkdsk", "disable_fastboot"],
                     ["Un écran bleu (BSOD) = Windows s'est arrêté d'urgence pour éviter des dégâts.",
                      "Causes, de la plus à la moins fréquente : pilote défectueux (carte graphique, Wi-Fi, antivirus), "
                      "fichiers Windows corrompus, barrette de RAM défaillante, disque qui lâche, surchauffe.",
                      "Le code d'erreur (ex. 0x0000007e, IRQL_NOT_LESS_OR_EQUAL) est dans les éléments relevés : "
                      "cherchez-le sur Internet avec le modèle du PC.",
                      "Que faire : 1) SFC puis DISM ; 2) mettre à jour le pilote graphique depuis le site du fabricant ; "
                      "3) tester la RAM (taper « mdsched » dans le menu Démarrer) ; 4) vérifier la santé du disque."])
        if kp > 2:
            return R("crashes", "Plantages", WARN, "%d arrêt(s) brutal(aux) ce mois-ci" % kp, det, ["disable_fastboot", "chkdsk"],
                     ["Le PC s'est éteint sans passer par « Arrêter » (sans écran bleu).",
                      "Causes : coupure de courant, bouton maintenu, batterie usée, bloc d'alimentation faible, ou surchauffe.",
                      "Que faire : vérifier la température (test « Processeur »), dépoussiérer, tester avec un autre "
                      "chargeur/bloc d'alimentation."])
        return R("crashes", "Plantages", OK, "Aucun plantage notable sur 30 jours", det)
    rc, out = run("journalctl -k --since '-30 days' --no-pager -q 2>/dev/null | grep -iE 'kernel panic|Oops:|BUG:|Out of memory|oom-kill|segfault|hardware error|mce:' | tail -n 20", timeout=40)
    n = len([l for l in out.splitlines() if l.strip()])
    if n == 0:
        return R("crashes", "Plantages", OK, "Aucun plantage noyau / manque mémoire détecté", "")
    st = ERROR if any(w in out for w in ("panic", "Oops", "hardware error", "mce:")) else WARN
    adv = []
    if "oom" in out.lower() or "out of memory" in out.lower():
        adv.append("« Out of memory » : la RAM a été saturée et Linux a tué des programmes pour survivre.")
    if "segfault" in out:
        adv.append("« segfault » : un programme a planté (bug ou bibliothèque cassée -> « Réparer le gestionnaire de paquets »).")
    if st == ERROR:
        adv.append("« panic / Oops / hardware error / mce » : plantage du noyau ou erreur matérielle (RAM, processeur). "
                   "Testez la RAM avec memtest86+ (menu GRUB) et vérifiez les températures.")
    return R("crashes", "Plantages", st, "%d incident(s) noyau ou manque de mémoire" % n, out, ["fix_packages"], adv)


def check_devices():
    if IS_WIN:
        data, raw = ps_json("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Error' -or $_.Status -eq 'Degraded' } | Select-Object FriendlyName,Class,Status,InstanceId", timeout=60)
        if data is None:
            return R("devices", "Périphériques / pilotes", INFO, "Lecture impossible", raw)
        if not data:
            return R("devices", "Périphériques / pilotes", OK, "Aucun périphérique en erreur")
        det = "\n".join("%s [%s] : %s\n    %s" % (d.get("FriendlyName") or "(inconnu)", d.get("Class"), d.get("Status"), d.get("InstanceId")) for d in data)
        names = ", ".join((d.get("FriendlyName") or "inconnu") for d in data[:3])
        return R("devices", "Périphériques / pilotes", WARN, "%d périphérique(s) en erreur : %s" % (len(data), names),
                 det, ["rescan_devices"],
                 ["Ces périphériques ne fonctionnent pas : pilote absent, mauvais pilote, ou matériel débranché/défectueux.",
                  "Selon la ligne « [Class] » : Net = réseau/Wi-Fi, Display = écran/carte graphique, Media = son, USB = ports USB, "
                  "Bluetooth, Camera…",
                  "Que faire : 1) « Rechercher les périphériques » ; 2) installer le pilote depuis le site du fabricant du PC "
                  "(rubrique Support, avec le modèle exact) ; 3) sans pilote connu, cherchez sur Internet le code "
                  "VEN_xxxx&DEV_xxxx de l'InstanceId."])
    rc, out = run("journalctl -k -b --no-pager -q 2>/dev/null | grep -iE 'firmware.*(failed|missing)|failed to load firmware|probe.*failed' | tail -n 15", timeout=20)
    if not out.strip():
        return R("devices", "Périphériques / pilotes", OK, "Aucun pilote/firmware manquant signalé")
    return R("devices", "Périphériques / pilotes", WARN, "Firmware ou pilote manquant", out, [],
             ["Un composant (souvent Wi-Fi, Bluetooth ou carte graphique) n'a pas trouvé son firmware.",
              "Que faire : installer le paquet linux-firmware (Fedora/Arch) ou firmware-linux-nonfree (Debian), "
              "puis redémarrer."])


def check_updates():
    if IS_WIN:
        import winreg
        pending = []
        for path, label in ((r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", "composants Windows"),
                            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", "Windows Update")):
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
                pending.append(label)
            except OSError:
                pass
        rc, last = run("(Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn.ToString('yyyy-MM-dd')", timeout=60)
        det = "Dernière mise à jour installée : %s" % (last or "inconnue")
        days = None
        try:
            days = (time.time() - time.mktime(time.strptime(last.strip(), "%Y-%m-%d"))) / 86400
        except ValueError:
            pass
        if pending:
            return R("updates", "Mises à jour", WARN, "Redémarrage en attente (%s)" % ", ".join(pending), det, ["reboot"],
                     ["Des mises à jour sont installées à moitié : elles ne seront finies qu'après un redémarrage.",
                      "En attendant, certaines installations et réparations échouent.",
                      "Que faire : redémarrer (pas « Arrêter »)."])
        if days is not None and days > 60:
            return R("updates", "Mises à jour", WARN, "Aucune mise à jour depuis %d jours : Windows Update est peut-être bloqué" % days,
                     det, ["reset_wu", "time_sync", "sfc"],
                     ["Windows n'a rien installé depuis plus de 2 mois : il manque des correctifs de sécurité.",
                      "Causes : Windows Update bloqué (cache corrompu), disque plein, date fausse, ou mises à jour désactivées.",
                      "Que faire : « Réparer Windows Update », puis Paramètres > Windows Update > Rechercher des mises à jour."])
        return R("updates", "Mises à jour", OK, "Windows est à jour", det)
    det, st, fixes, summ, adv = "", OK, [], "Système à jour (selon le cache local)", []
    if os.path.exists("/var/run/reboot-required"):
        st, summ, fixes = WARN, "Redémarrage requis après mise à jour", ["reboot"]
        adv.append("Un nouveau noyau ou une bibliothèque importante a été installé : redémarrez pour l'utiliser.")
    if which("dpkg"):
        rc, audit = run("dpkg --audit", timeout=30)
        if audit.strip():
            st, summ = ERROR, "Paquets mal installés (installation interrompue)"
            det += audit + "\n"
            fixes = ["fix_packages"] + fixes
            adv.insert(0, "Une installation/mise à jour a été interrompue (coupure, fermeture du terminal) : "
                          "tant que ce n'est pas réparé, plus aucune installation ne fonctionne. "
                          "Que faire : « Réparer le gestionnaire de paquets ».")
        rc, up = run("apt list --upgradable 2>/dev/null | grep -c upgradable", timeout=30)
        if up.strip().isdigit() and int(up) > 0:
            det += "%s paquet(s) à mettre à jour\n" % up.strip()
            if st == OK:
                st, summ = INFO, "%s mise(s) à jour disponible(s)" % up.strip()
            fixes.append("update_system")
    return R("updates", "Mises à jour / paquets", st, summ, det, fixes, adv)


def check_security():
    if IS_WIN:
        data, raw = ps_json("Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureAge,QuickScanAge")
        av, raw2 = ps_json("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction SilentlyContinue | Select-Object displayName,productState")
        fw, raw3 = ps_json("Get-NetFirewallProfile | Select-Object Name,Enabled")
        names = [a.get("displayName") for a in (av or [])]
        det = "Antivirus installés : %s\n" % (", ".join(n for n in names if n) or "aucun détecté")
        problems, fixes = [], []
        if data:
            d = data[0]
            det += "Defender : temps réel=%s, signatures âgées de %s jour(s), dernière analyse il y a %s jour(s)\n" % (
                d.get("RealTimeProtectionEnabled"), d.get("AntivirusSignatureAge"), d.get("QuickScanAge"))
            third = [n for n in names if n and "defender" not in n.lower()]
            if not d.get("RealTimeProtectionEnabled") and not third:
                problems.append("aucune protection antivirus active")
            if (d.get("AntivirusSignatureAge") or 0) > 7 and not third:
                problems.append("signatures antivirus périmées")
                fixes.append("defender_update")
            fixes.append("defender_scan")
        if fw:
            det += "Pare-feu : " + ", ".join("%s=%s" % (f.get("Name"), "actif" if f.get("Enabled") in (True, 1) else "INACTIF") for f in fw)
            if any(f.get("Enabled") in (False, 0) for f in fw):
                problems.append("pare-feu désactivé")
                fixes.append("firewall_on")
        rc, proxy = run("(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings').ProxyEnable", timeout=10)
        if proxy.strip() == "1":
            problems.append("proxy actif (possible malware)")
            fixes.append("reset_proxy")
        if problems:
            adv = []
            if "aucune protection antivirus active" in problems:
                adv.append("Aucun antivirus ne protège le PC en temps réel. Si vous ne l'avez pas désactivé vous-même, "
                           "c'est un signe classique de malware.")
            if "signatures antivirus périmées" in problems:
                adv.append("L'antivirus ne connaît pas les menaces récentes : mettez à jour les signatures.")
            if "pare-feu désactivé" in problems:
                adv.append("Le pare-feu est coupé : le PC est exposé sur les réseaux publics. Réactivez-le.")
            if "proxy actif (possible malware)" in problems:
                adv.append("Un proxy redirige la navigation : souvent laissé par un adware ou un malware. Supprimez-le "
                           "et lancez une analyse antivirus.")
            return R("security", "Sécurité", WARN, "Attention : " + ", ".join(problems), det, fixes, adv)
        return R("security", "Sécurité", OK, "Antivirus et pare-feu actifs", det, fixes)
    out = ""
    if which("ufw"):
        out += run("ufw status", timeout=10)[1] + "\n"
    if which("firewall-cmd"):
        out += "firewalld : " + run("firewall-cmd --state", timeout=10)[1] + "\n"
    rc, users = run("awk -F: '$3==0{print $1}' /etc/passwd", timeout=5)
    out += "Comptes avec droits root (UID 0) : " + users.replace("\n", ", ")
    st = WARN if users.strip() != "root" else INFO
    return R("security", "Sécurité", st, "Compte root supplémentaire suspect !" if st == WARN else "Pas d'anomalie évidente", out, [],
             ["Un compte autre que « root » possède les pleins pouvoirs : c'est typique d'une intrusion. "
              "Vérifiez /etc/passwd et changez les mots de passe."] if st == WARN else [])


def check_startup():
    if IS_WIN:
        data, raw = ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location")
        data = data or []
        det = "\n".join("- %s : %s" % (d.get("Name"), d.get("Command")) for d in data)
        st = WARN if len(data) > 12 else INFO
        return R("startup", "Programmes au démarrage", st, "%d programme(s) lancé(s) au démarrage%s" %
                 (len(data), " : démarrage probablement lent" if st == WARN else ""),
                 det, ["startup_manager"] if st == WARN else [],
                 ["Chaque programme de la liste se lance à chaque allumage : le PC met longtemps à être utilisable "
                  "et la mémoire est occupée pour rien.",
                  "Que faire : ouvrir le gestionnaire de démarrage et désactiver ce qui n'est pas indispensable "
                  "(Steam, Discord, Spotify, Teams, lanceurs d'imprimante…). Gardez l'antivirus et les pilotes "
                  "(Realtek, Intel, NVIDIA, AMD, pavé tactile)."] if st == WARN else [])
    if not which("systemd-analyze"):
        return R("startup", "Démarrage", INFO, "systemd-analyze indisponible")
    rc, t = run("systemd-analyze 2>/dev/null | head -n 1", timeout=20)
    rc, b = run("systemd-analyze blame 2>/dev/null | head -n 12", timeout=20)
    return R("startup", "Démarrage", INFO, t.strip() or "Temps de démarrage inconnu", b)


def check_time():
    if IS_WIN:
        rc, out = run("w32tm /query /status", shell="cmd", timeout=15)
    else:
        rc, out = run("timedatectl 2>/dev/null", timeout=10)
    year = time.localtime().tm_year
    if year < 2024:
        return R("time", "Date et heure", ERROR, "La date du PC est fausse (%s) : sites HTTPS et mises à jour en échec (pile BIOS ?)"
                 % time.strftime("%d/%m/%Y"), out, ["time_sync"],
                 ["Avec une date fausse, les sites sécurisés refusent la connexion (« Votre connexion n'est pas privée ») "
                  "et les mises à jour échouent.",
                  "Si la date revient à une vieille valeur à chaque allumage : la pile de la carte mère (CR2032) est vide, "
                  "il faut la changer (quelques euros)."])
    return R("time", "Date et heure", INFO, "Date du PC : %s" % time.strftime("%d/%m/%Y %H:%M"), out, ["time_sync"])


ALL_CHECKS = [check_system, check_memory, check_disks, check_disk_health, check_network,
              check_services, check_crashes, check_devices, check_updates, check_security, check_logs,
              check_startup, check_time, check_processes]


def _safe(fn):
    try:
        return fn()
    except Exception as e:
        return R(fn.__name__, fn.__name__.replace("check_", "").capitalize(), INFO, "Test impossible : %s" % e)


def run_all(progress=None):
    """progress(résultat, n_terminés, n_total) est appelé à chaque test terminé."""
    total = len(ALL_CHECKS) + 1
    # Le processeur est mesuré seul, AVANT les autres tests, sinon on mesurerait le diagnostic lui-même.
    results = [_safe(check_cpu)]
    if progress:
        progress(results[0], 1, total)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_safe, fn) for fn in ALL_CHECKS]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
            if progress:
                progress(results[-1], len(results), total)
    return results
