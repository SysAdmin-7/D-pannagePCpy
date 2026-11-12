# DépannagePC

Outil de dépannage à utiliser **directement sur le PC en panne** (Windows ou Linux), depuis une clé USB.
Aucune connexion à un autre PC, aucune dépendance (Python 3.8+ uniquement).

## Utilisation
1. Lancez DépannagePC sur le PC en panne (il demande les droits administrateur).
2. Menu :
   - **1) Diagnostic complet** : 15 tests (disque, santé SMART, RAM, processeur, réseau/Internet, services,
     écrans bleus/plantages, pilotes, mises à jour, antivirus/pare-feu, journaux, démarrage, heure…).
     Chaque problème s'affiche automatiquement sous forme de fiche : constat, explication en clair
     (cause probable, conséquence, que faire), éléments relevés sur le PC et réparations proposées.
     Les erreurs des journaux Windows sont classées GRAVE / à surveiller / sans gravité.
     Ensuite : `r` = réparation guidée (toutes les réparations conseillées, une par une, avec un point
     de restauration d'abord), numéro = fiche complète d'un test, `s` = enregistrer le rapport.
   - **2) Réparations** : catalogue classé (Réseau, Système, Disque, Nettoyage, Sécurité, Mises à jour, Alimentation).
     Chaque réparation affiche sa commande et ses risques, et demande confirmation avant de s'exécuter.
   - **3) Rapport** : enregistre le dernier diagnostic dans un fichier texte sur le Bureau.
3. `Ctrl+C` interrompt une réparation en cours.

## Lancer
- Windows : `lancer.bat` (Python requis) ou `DepannagePC.exe` (voir ci-dessous)
- Linux : `sudo ./lancer.sh`

## Créer l'exécutable pour clé USB
- Windows : `build_windows.bat` → `dist\DepannagePC.exe`
- Linux (à construire sur Linux) : `sh build_linux.sh` → `dist/DepannagePC`

## Fichiers
| Fichier | Rôle |
|---|---|
| `depannage.py` | Lanceur |
| `dpc/local.py` | Menus et exécution des réparations |
| `dpc/checks.py` | Tests de diagnostic |
| `dpc/actions.py` | Catalogue des réparations |
| `dpc/system.py` | Fonctions communes Windows/Linux |

## Limites
- Le PC doit démarrer jusqu'au système. Sinon, démarrez sur une clé Linux live et lancez `lancer.sh` depuis celle-ci.
- Les pannes matérielles (disque mort, RAM, alimentation) sont détectées, pas réparées.
- Créez un point de restauration (Réparations > Système) avant toute réparation importante sous Windows.
