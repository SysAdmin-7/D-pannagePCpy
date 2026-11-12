"""Catalogue des réparations exécutables à distance, par système."""
from .system import IS_WIN

CRITICAL_WIN_SERVICES = ["Dhcp", "Dnscache", "EventLog", "Winmgmt", "RpcSs", "LanmanWorkstation",
                         "Spooler", "Audiosrv", "AudioEndpointBuilder", "BFE", "mpssvc", "nsi",
                         "NlaSvc", "CryptSvc", "Schedule", "ProfSvc", "Themes", "PlugPlay", "Power",
                         "BrokerInfrastructure", "SENS", "UserManager"]

_SVC_LIST = ",".join("'%s'" % s for s in CRITICAL_WIN_SERVICES)


def A(id, cat, label, desc, cmd, shell, warning="", internet=False):
    return {"id": id, "category": cat, "label": label, "desc": desc, "cmd": cmd, "shell": shell,
            "warning": warning, "internet": internet}


WINDOWS = [
    # --- Réseau
    A("flush_dns", "Réseau", "Vider le cache DNS", "Corrige les sites qui ne s'ouvrent plus ou pointent au mauvais endroit.",
      "ipconfig /flushdns", "cmd"),
    A("renew_ip", "Réseau", "Renouveler l'adresse IP", "Redemande une adresse au routeur (DHCP).",
      "ipconfig /release & ipconfig /renew", "cmd",
      "Coupe brièvement les connexions réseau (le lien avec le technicien peut sauter quelques secondes)."),
    A("reset_network", "Réseau", "Réinitialiser la pile réseau (Winsock/TCP-IP)",
      "Répare les problèmes réseau profonds (pas d'Internet alors que tout semble connecté).",
      "netsh winsock reset & netsh int ip reset & ipconfig /flushdns", "cmd",
      "Redémarrage nécessaire ensuite."),
    A("set_dns_public", "Réseau", "Utiliser des DNS publics (1.1.1.1 / 8.8.8.8)",
      "Contourne une box ou un FAI dont les DNS ne répondent plus.",
      "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ('1.1.1.1','8.8.8.8'); Write-Output ('DNS modifiés sur ' + $_.Name) }",
      "ps"),
    A("reset_dns_auto", "Réseau", "Remettre les DNS en automatique", "Annule la modification des DNS.",
      "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses; Write-Output ('DNS automatiques sur ' + $_.Name) }",
      "ps"),
    A("reset_proxy", "Réseau", "Supprimer le proxy système", "Un proxy (souvent laissé par un malware) bloque Internet.",
      "netsh winhttp reset proxy; Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' -Name ProxyEnable -Value 0; Write-Output 'Proxy désactivé'",
      "ps"),
    # --- Système
    A("restore_point", "Système", "Créer un point de restauration", "À faire AVANT toute réparation importante.",
      "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\"; Checkpoint-Computer -Description 'DepannagePC' -RestorePointType MODIFY_SETTINGS; Write-Output 'Point de restauration créé (ou déjà créé il y a moins de 24h).'",
      "ps"),
    A("sfc", "Système", "Réparer les fichiers système (SFC)", "Vérifie et remplace les fichiers Windows corrompus.",
      "sfc /scannow", "cmd", "Dure 10 à 30 minutes."),
    A("dism", "Système", "Réparer l'image Windows (DISM)", "Répare le magasin de composants (à lancer si SFC échoue).",
      "DISM /Online /Cleanup-Image /RestoreHealth", "cmd", "Dure 10 à 40 minutes, Internet conseillé.", True),
    A("start_services", "Système", "Redémarrer les services essentiels arrêtés",
      "Relance réseau, son, impression, pare-feu, etc. s'ils sont arrêtés.",
      "Get-Service -Name %s -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Running' -and $_.StartType -ne 'Disabled' } | ForEach-Object { try { Start-Service $_.Name -ErrorAction Stop; Write-Output ('Démarré : ' + $_.Name) } catch { Write-Output ('Échec : ' + $_.Name + ' - ' + $_.Exception.Message) } }; Write-Output 'Terminé.'" % _SVC_LIST,
      "ps"),
    A("restart_explorer", "Système", "Redémarrer l'Explorateur", "Barre des tâches / bureau figés ou invisibles.",
      "Stop-Process -Name explorer -Force; Start-Sleep 3; if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }; Write-Output 'Explorateur relancé'",
      "ps"),
    A("restart_spooler", "Système", "Débloquer l'impression", "Vide la file d'impression et relance le spouleur.",
      "Stop-Service Spooler -Force; Remove-Item \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force -ErrorAction SilentlyContinue; Start-Service Spooler; Write-Output 'File d''impression vidée, spouleur relancé.'",
      "ps"),
    A("rescan_devices", "Système", "Rechercher les périphériques / pilotes", "Relance la détection matérielle (périphérique non reconnu).",
      "pnputil /scan-devices", "cmd"),
    A("time_sync", "Système", "Resynchroniser l'heure", "Une heure fausse casse HTTPS, Windows Update, etc.",
      "net start w32time & w32tm /resync /force", "cmd", internet=True),
    A("startup_manager", "Système", "Gérer les programmes au démarrage",
      "Ouvre le Gestionnaire des tâches sur l'onglet Démarrage pour désactiver les programmes inutiles.",
      "Start-Process taskmgr.exe -ArgumentList '/0 /startup'; Write-Output 'Gestionnaire des tâches ouvert : clic droit > Désactiver sur les programmes inutiles.'",
      "ps"),
    A("disable_fastboot", "Système", "Désactiver le démarrage rapide",
      "Corrige beaucoup de problèmes d'arrêt/démarrage et de périphériques après extinction.",
      "powercfg /h off & echo Demarrage rapide et hibernation desactives.", "cmd"),
    # --- Disque
    A("chkdsk", "Disque", "Vérifier le disque au prochain démarrage (CHKDSK)",
      "Marque le disque système pour une vérification complète au redémarrage.",
      "fsutil dirty set %SystemDrive% & echo Verification programmee au prochain demarrage.", "cmd",
      "La vérification se fera au redémarrage et peut être longue."),
    A("disk_health", "Disque", "État SMART détaillé des disques", "Affiche l'usure et les erreurs des disques.",
      "Get-PhysicalDisk | ForEach-Object { $_ | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus; $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue | Select-Object Temperature,Wear,ReadErrorsTotal,WriteErrorsTotal,PowerOnHours } | Format-List | Out-String -Width 200",
      "ps"),
    # --- Nettoyage
    A("clean_temp", "Nettoyage", "Supprimer les fichiers temporaires", "Libère de la place (TEMP utilisateur et Windows).",
      "$before=(Get-PSDrive C).Free; Remove-Item \"$env:TEMP\\*\",\"$env:SystemRoot\\Temp\\*\" -Recurse -Force -ErrorAction SilentlyContinue; Get-ChildItem 'C:\\Users\\*\\AppData\\Local\\Temp' -Directory -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item \"$($_.FullName)\\*\" -Recurse -Force -ErrorAction SilentlyContinue }; $gain=[math]::Round(((Get-PSDrive C).Free-$before)/1MB); Write-Output \"Espace libéré : $gain Mo\"",
      "ps"),
    A("clean_update_cache", "Nettoyage", "Nettoyer les composants Windows", "Supprime les anciennes versions de mises à jour.",
      "DISM /Online /Cleanup-Image /StartComponentCleanup", "cmd", "Peut durer plusieurs minutes."),
    A("empty_recycle", "Nettoyage", "Vider la corbeille (tous les utilisateurs)", "Libère de l'espace disque.",
      "Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Output 'Corbeille vidée'", "ps",
      "Les fichiers de la corbeille seront définitivement perdus."),
    # --- Sécurité
    A("defender_scan", "Sécurité", "Analyse antivirus rapide (Defender)", "Recherche de logiciels malveillants.",
      "Start-MpScan -ScanType QuickScan; Get-MpThreatDetection | Select-Object -First 20 | Format-List | Out-String; Write-Output 'Analyse terminée.'",
      "ps", "Dure quelques minutes."),
    A("defender_update", "Sécurité", "Mettre à jour les signatures antivirus", "", "Update-MpSignature; Write-Output 'Signatures à jour.'",
      "ps", internet=True),
    A("firewall_on", "Sécurité", "Réactiver le pare-feu Windows", "",
      "Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True; Write-Output 'Pare-feu activé'", "ps"),
    # --- Mises à jour
    A("reset_wu", "Mises à jour", "Réparer Windows Update", "Remet à zéro les caches de Windows Update bloqués.",
      "net stop wuauserv /y & net stop bits /y & net stop cryptsvc /y & ren %SystemRoot%\\SoftwareDistribution SoftwareDistribution.old%RANDOM% & ren %SystemRoot%\\System32\\catroot2 catroot2.old%RANDOM% & net start cryptsvc & net start bits & net start wuauserv & echo Windows Update reinitialise.",
      "cmd"),
    A("winget_upgrade", "Mises à jour", "Mettre à jour les logiciels (winget)", "Met à jour les applications installées.",
      "winget upgrade --all --silent --accept-source-agreements --accept-package-agreements", "cmd",
      "Peut être long.", True),
    # --- Alimentation
    A("reboot", "Alimentation", "Redémarrer dans 60 s", "", "shutdown /r /t 60 /c \"Redemarrage demande par le technicien\"", "cmd",
      "Le PC va redémarrer : la connexion sera perdue."),
    A("reboot_safe", "Alimentation", "Redémarrer en mode sans échec (réseau)", "Pour supprimer un pilote/logiciel qui empêche Windows de démarrer.",
      "bcdedit /set {current} safeboot network & shutdown /r /t 30", "cmd",
      "Utilisez ensuite « Quitter le mode sans échec » pour revenir à la normale."),
    A("exit_safe", "Alimentation", "Quitter le mode sans échec", "", "bcdedit /deletevalue {current} safeboot & echo OK - redemarrez normalement.", "cmd"),
    A("cancel_reboot", "Alimentation", "Annuler un redémarrage programmé", "", "shutdown /a", "cmd"),
]

LINUX = [
    # --- Réseau
    A("flush_dns", "Réseau", "Vider le cache DNS", "",
      "resolvectl flush-caches 2>/dev/null || systemd-resolve --flush-caches 2>/dev/null; echo 'Cache DNS vidé (si systemd-resolved est utilisé).'", "sh"),
    A("restart_network", "Réseau", "Redémarrer le réseau", "Relance NetworkManager / systemd-networkd.",
      "if systemctl is-active -q NetworkManager; then systemctl restart NetworkManager; echo NetworkManager relancé; elif systemctl is-active -q systemd-networkd; then systemctl restart systemd-networkd; echo networkd relancé; else echo 'Aucun gestionnaire réseau connu actif'; fi",
      "sh", "Le lien avec le technicien peut sauter quelques secondes."),
    A("set_dns_public", "Réseau", "Utiliser des DNS publics (1.1.1.1 / 8.8.8.8)", "",
      "if command -v resolvectl >/dev/null && systemctl is-active -q systemd-resolved; then for i in $(ip -o link show up | awk -F': ' '{print $2}' | grep -v lo); do resolvectl dns \"$i\" 1.1.1.1 8.8.8.8; done; echo 'DNS publics appliqués (temporaire)'; else cp /etc/resolv.conf /etc/resolv.conf.bak-depannage; printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf; echo 'resolv.conf remplacé (sauvegarde : /etc/resolv.conf.bak-depannage)'; fi",
      "sh"),
    # --- Système
    A("restart_failed", "Système", "Relancer les services en échec", "Redémarre chaque service systemd en échec.",
      "for u in $(systemctl --failed --no-legend --plain | awk '{print $1}'); do echo \"--> $u\"; systemctl restart \"$u\" && echo OK || systemctl status \"$u\" --no-pager -n 5; done; systemctl reset-failed; echo Terminé.",
      "sh"),
    A("fix_packages", "Système", "Réparer le gestionnaire de paquets", "Termine les installations interrompues, corrige les dépendances.",
      "if command -v apt-get >/dev/null; then dpkg --configure -a && apt-get -f install -y; elif command -v dnf >/dev/null; then rpm --rebuilddb && dnf -y check; elif command -v pacman >/dev/null; then rm -f /var/lib/pacman/db.lck; pacman -Dk; elif command -v zypper >/dev/null; then zypper -n verify; fi; echo Terminé.",
      "sh"),
    A("update_system", "Mises à jour", "Mettre à jour le système", "",
      "if command -v apt-get >/dev/null; then apt-get update && apt-get -y full-upgrade; elif command -v dnf >/dev/null; then dnf -y upgrade --refresh; elif command -v pacman >/dev/null; then pacman -Syu --noconfirm; elif command -v zypper >/dev/null; then zypper -n refresh && zypper -n update; fi",
      "sh", "Peut être long.", True),
    A("update_grub", "Système", "Régénérer le menu de démarrage (GRUB)", "Après un noyau ou un double-boot cassé.",
      "if command -v update-grub >/dev/null; then update-grub; elif command -v grub2-mkconfig >/dev/null; then grub2-mkconfig -o /boot/grub2/grub.cfg; else grub-mkconfig -o /boot/grub/grub.cfg; fi",
      "sh"),
    A("rebuild_initramfs", "Système", "Régénérer l'initramfs", "Démarrage bloqué après une mise à jour du noyau.",
      "if command -v update-initramfs >/dev/null; then update-initramfs -u -k all; elif command -v dracut >/dev/null; then dracut --regenerate-all --force; elif command -v mkinitcpio >/dev/null; then mkinitcpio -P; fi",
      "sh"),
    A("restart_display", "Système", "Redémarrer l'interface graphique", "Écran figé / session graphique plantée.",
      "systemctl restart display-manager", "sh", "Ferme la session graphique ouverte (travail non enregistré perdu)."),
    A("time_sync", "Système", "Resynchroniser l'heure", "", "timedatectl set-ntp true; systemctl restart systemd-timesyncd 2>/dev/null || chronyc makestep 2>/dev/null; timedatectl", "sh", internet=True),
    # --- Disque
    A("disk_health", "Disque", "État SMART détaillé des disques", "",
      "if ! command -v smartctl >/dev/null; then echo 'smartctl absent : installez smartmontools'; exit 0; fi; for d in $(smartctl --scan | awk '{print $1}'); do echo \"===== $d\"; smartctl -H -A \"$d\"; done",
      "sh"),
    A("fsck_boot", "Disque", "Vérifier les disques au prochain démarrage", "",
      "touch /forcefsck; (command -v tune2fs >/dev/null && ROOT=$(findmnt -no SOURCE /) && tune2fs -C 1000 \"$ROOT\" >/dev/null 2>&1); echo 'Vérification programmée au prochain démarrage.'",
      "sh"),
    A("remount_rw", "Disque", "Remonter / en lecture-écriture", "Le système est passé en lecture seule.",
      "mount -o remount,rw / && echo OK", "sh", "Si le disque est passé en lecture seule, il est probablement en train de lâcher : sauvegardez d'abord !"),
    # --- Nettoyage
    A("clean_space", "Nettoyage", "Libérer de l'espace disque", "Journaux, cache des paquets, fichiers temporaires anciens.",
      "df -h / | tail -1; journalctl --vacuum-size=200M; if command -v apt-get >/dev/null; then apt-get clean; apt-get -y autoremove; elif command -v dnf >/dev/null; then dnf clean all; elif command -v pacman >/dev/null; then pacman -Sc --noconfirm; fi; find /tmp /var/tmp -type f -atime +3 -delete 2>/dev/null; df -h / | tail -1",
      "sh"),
    # --- Alimentation
    A("reboot", "Alimentation", "Redémarrer dans 1 minute", "", "shutdown -r +1 'Redémarrage demandé par le technicien'", "sh",
      "Le PC va redémarrer : la connexion sera perdue."),
    A("cancel_reboot", "Alimentation", "Annuler un redémarrage programmé", "", "shutdown -c", "sh"),
]


def catalog():
    return WINDOWS if IS_WIN else LINUX


def get(action_id):
    for a in catalog():
        if a["id"] == action_id:
            return a
    return None
