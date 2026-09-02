# 🎨 dbmltool

Convertit un fichier **DBML** (dialecte maison, orienté Oracle) en script **SQL Oracle** exécutable (`CREATE TABLE`, index, contraintes, commentaires, `GRANT`). ✨

Outil en ligne de commande, écrit en Python 3.13, sans dépendance externe (bibliothèque standard uniquement). 🐍

## Sommaire 📚

- [Cahier des charges](#cahier-des-charges-)
- [Spécifications](#spécifications-)
- [Subtilités d'implémentation](#subtilités-dimplémentation-)
- [Exploitation](#exploitation-)
- [Structure du projet](#structure-du-projet-)
- [Limites connues](#limites-connues-)

## Cahier des charges 📝

Le projet a été construit de façon itérative. Voici les besoins exprimés, dans l'ordre :

1. **Conversion DBML → SQL.** Fournir un programme Python (venv dédié, Python 3.13) qui transforme un fichier `.dbml` en fichier `.sql`, en reproduisant le style SQL Oracle attendu (`DROP TABLE`, `CREATE TABLE`, index unique + contrainte `PRIMARY KEY`, `COMMENT ON TABLE`/`COLUMN`, `GRANT`).
2. **Vérification de cohérence.** Pouvoir vérifier qu'un fichier `.sql` existant correspond bien au fichier `.dbml` dont il est censé provenir (utile pour repérer les incohérences introduites manuellement).
3. **Séparateur entre tables.** Générer un commentaire de séparation (`-- ---...`) entre chaque table dans le fichier SQL produit, pour la lisibilité.
4. **Tablespace obligatoire.** Le tablespace est obligatoire pour chaque table et chaque index : s'il manque, la génération doit être **refusée** (aucun fichier écrit) avec un message précis indiquant la table/l'index fautif. En revanche, la description de table, les notes de colonne et les `grants` restent **optionnels**.
5. **Relations ignorées.** Les relations DBML (`Ref: ...`) doivent être ignorées silencieusement (non supportées pour le moment).
6. **Tests unitaires.** Fournir une suite de tests Python couvrant le parsing, la validation et le rendu.
7. **Automatisation.** Fournir un `Makefile` avec au moins `make run` (génération) et `make tests` (tests).
8. **Valeurs par défaut de fichier.** Permettre de déclarer, sur une seule table du fichier, `default_tablespace`/`default_grants` à la place de `tablespace`/`grants`. Ces valeurs sont appliquées à toute table qui n'a pas sa propre valeur (et qui, sans cela, provoquerait une erreur de tablespace manquant). De même, un seul index du fichier peut déclarer `default_tablespace`, appliqué à tout index sans tablespace propre. Déclarer une valeur par défaut plusieurs fois dans le fichier est une erreur.
9. **Erreurs d'entrée/sortie gérées proprement.** Un fichier d'entrée introuvable/illisible, ou un fichier de sortie impossible à créer (répertoire inexistant, permissions insuffisantes, chemin invalide), doivent produire un message d'erreur clair sur la sortie d'erreur et un code de sortie non nul — jamais une trace Python brute (`Traceback`).

## Spécifications 🔧

### Format DBML supporté

```dbml
Table NOM_TABLE [note: "descr: <description>, tablespace: <TS>, grants: [CODE:LETTRES,CODE:LETTRES,...]"] {
  COLONNE1 char(1)
  COLONNE2 integer [note: "commentaire de colonne"]
  COLONNE3 varchar2(100) [note: "commentaire"]
  COLONNE4 number(11,2)

  indexes {
    (COLONNE1, COLONNE2) [pk, name: "NOM_INDEX", note: "tablespace: <TS_INDEX>"]
  }
}

Ref: AUTRE_TABLE.(COL) < NOM_TABLE.(COL)   -- ignoré
```

- **Table** : `Table NOM [note: "..."] { ... }`. La note est optionnelle globalement, mais son absence de `tablespace`/`default_tablespace` provoque un refus de génération (voir plus bas).
  - `descr` — description de la table → `COMMENT ON TABLE`. **Optionnel.**
  - `tablespace` — tablespace de la table → clause `TABLESPACE` du `CREATE TABLE`. **Obligatoire** (directement ou via `default_tablespace`, voir plus bas).
  - `grants` — droits d'accès, format `[CODE_ROLE:LETTRES,...]` où les lettres combinent `D` (DELETE), `I` (INSERT), `S` (SELECT), `U` (UPDATE) → une ligne `GRANT ... TO ...` par lettre présente, dans l'ordre fixe D, I, S, U. **Optionnel.**
- **Colonnes** : `NOM TYPE [note: "commentaire"]`. Le type est recopié tel quel (mis en majuscules) : `char(1)`, `integer`, `number(4)`, `number(11,2)`, `varchar2(100)`, `date`, etc. La note devient un `COMMENT ON COLUMN`. **Optionnelle.**
- **Index** (bloc `indexes { ... }`) : `(COL1, COL2) [pk, unique, name: "NOM", note: "tablespace: <TS>"]`.
  - `pk` ou `unique` → génère `CREATE UNIQUE INDEX` (sinon `CREATE INDEX` simple).
  - `pk` → génère en plus `ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY ... USING INDEX ... ENABLE`.
  - `name` — nom de l'index/contrainte (sinon un nom `<TABLE>_IDX` est généré).
  - `tablespace` (dans la note) — **obligatoire** (directement ou via `default_tablespace`).
- **Relations** (`Ref: ...`) : lignes reconnues et **ignorées** silencieusement, où qu'elles apparaissent dans le fichier.

### Valeurs par défaut de fichier (`default_tablespace` / `default_grants`)

Sur la note d'**une seule table** du fichier, `tablespace`/`grants` peuvent être remplacés par `default_tablespace`/`default_grants`. Sur la note d'**un seul index** du fichier, `tablespace` peut être remplacé par `default_tablespace`. Ces trois défauts sont indépendants (déclarés ou non, ensemble ou séparément).

Règles d'application (dans cet ordre) :

1. **Résolution** : le programme parcourt tout le fichier pour repérer les déclarations `default_tablespace` (table), `default_grants` et `default_tablespace` (index).
2. **Unicité** : si un de ces défauts est déclaré plus d'une fois dans le fichier, la génération est **refusée** avec un message listant les tables/index en conflit — *aucun* défaut n'est appliqué dans ce cas.
3. **Application** : chaque défaut trouvé est appliqué à toute table (ou index) qui n'a pas sa propre valeur — **y compris à la table/l'index qui déclare le défaut lui-même**, s'il ne fournit pas par ailleurs sa propre valeur explicite.
4. **Priorité** : une valeur explicite (`tablespace`/`grants` propres à une table ou un index) n'est jamais écrasée par un défaut.

Cette résolution a lieu *avant* la vérification du tablespace obligatoire : une table sans tablespace propre ne déclenche donc une erreur que si aucun `default_tablespace` n'est disponible dans le fichier.

### Règles de validation obligatoires 🛡️

| Élément | Obligatoire ? | Si absent |
|---|---|---|
| Tablespace de table (`tablespace` ou `default_tablespace`) | **Oui** | Génération refusée |
| Tablespace d'index (`tablespace` ou `default_tablespace`) | **Oui** | Génération refusée |
| Description de table (`descr`) | Non | Pas de `COMMENT ON TABLE` |
| Commentaire de colonne (`note`) | Non | Pas de `COMMENT ON COLUMN` pour cette colonne |
| Grants (`grants` ou `default_grants`) | Non | Pas de `GRANT` généré |
| Déclaration unique de chaque défaut | **Oui** | Génération refusée |

Quand la génération est refusée, **aucun fichier de sortie n'est écrit** (le fichier existant, s'il y en a un, n'est pas modifié) et le code de sortie du programme est `1`. La ou les causes précises sont listées sur la sortie d'erreur.

### Sortie SQL générée

Pour chaque table, dans l'ordre : `DROP TABLE`, `CREATE TABLE` (colonnes alignées, clause `TABLESPACE`), puis pour chaque index `CREATE [UNIQUE] INDEX` (+ `ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY` si `pk`), `COMMENT ON TABLE`, `COMMENT ON COLUMN` (une ligne par colonne commentée), puis les `GRANT`. Les tables sont séparées par un commentaire `-- ---...---` entouré de lignes vides.

### Erreurs d'entrée/sortie ⚠️

En plus des erreurs de validation du contenu DBML, deux cas d'erreur liés au système de fichiers sont gérés explicitement (pas de trace Python brute) :

| Cas | Comportement |
|---|---|
| Fichier d'entrée introuvable ou illisible | Message `Erreur : impossible de lire le fichier d'entrée <chemin> (<raison>).` sur stderr, code de sortie `1`. |
| Fichier de sortie impossible à créer (répertoire inexistant, chemin pointant vers un répertoire, permissions insuffisantes, etc.) | Message `Erreur : impossible d'écrire le fichier de sortie <chemin> (<raison>).` sur stderr, code de sortie `1`. |

Dans les deux cas, aucun fichier de sortie partiel ou corrompu n'est laissé sur le disque.

## Subtilités d'implémentation 🔍

Quelques points qui ne sont pas évidents à la première lecture du code (`dbml2sql.py`) :

- **Parsing des notes par clé indépendante, pas par regex figée.** Une première version parsait la note de table avec une unique regex attendant `descr, tablespace, grants` dans cet ordre précis. Cela cassait dès qu'un seul champ était présent (ex. `tablespace` seul) : le motif ne matchait plus du tout. Le parsing extrait maintenant chaque clé indépendamment (`TABLE_NOTE_KEY_RE`), peu importe l'ordre ou le sous-ensemble de clés présentes.
- **`default_tablespace` vs `tablespace` : collision de sous-chaîne.** `"default_tablespace"` contient littéralement `"tablespace"`. Une regex naïve cherchant `tablespace\s*:` matcherait à l'intérieur de `default_tablespace:`, absorbant le préfixe `default_` et corrompant le parsing. Corrigé en déclarant `default_tablespace`/`default_grants` comme clés à part entière dans l'alternance de la regex (au niveau table), et avec un lookbehind négatif `(?<!default_)` pour la regex de tablespace d'index.
- **Capture de valeur trop gourmande.** La regex d'extraction du tablespace d'index utilisait `\S+`, qui capturait aussi une virgule de séparation quand la note contient deux champs (`"tablespace: TS, default_tablespace: ID_TS"` → capturait `TS,` au lieu de `TS`). Corrigé avec `[^,\s]+`.
- **Tolérance sur les crochets des `grants`.** Le format `grants: [CODE:LETTRES,...]` a été observé dans la pratique avec et sans crochet fermant (`]`) — la citation `"..."` du DBML se referme parfois avant le `]`. Le parsing (`parse_grants`) fait un `lstrip("[").rstrip("]")` tolérant : les deux variantes produisent un résultat strictement identique.
- **Application des défauts avant validation.** `apply_defaults()` s'exécute avant `validate_table()` dans `main()` : une table ou un index sans valeur propre reçoit d'abord la valeur par défaut disponible, et n'est donc signalé en erreur que si aucun défaut ne peut la combler.
- **Auto-application du défaut à sa propre table/index source.** Rien ne dispense la table (ou l'index) qui *déclare* `default_tablespace`/`default_grants` d'en bénéficier elle-même si elle ne fournit pas par ailleurs sa propre valeur explicite — comportement volontaire et testé (`test_source_table_receives_its_own_default`).
- **Encodage de la console Windows.** Les caractères accentués (français) dans les messages d'erreur/succès s'affichaient corrompus selon la page de code active du terminal Windows (ex. `850`). Le script force `stdout`/`stderr` en UTF-8 via `reconfigure()` sur `win32`.
- **`OSError` capturée largement plutôt que par cas précis.** `FileNotFoundError`, `PermissionError`, `IsADirectoryError`, `NotADirectoryError` sont toutes des sous-classes d'`OSError` : plutôt que de lister chaque cas concret (fichier absent, permissions, chemin de sortie qui est un répertoire, répertoire parent manquant...), la lecture de l'entrée et l'écriture de la sortie sont chacune protégées par un unique `except OSError`, avec `e.strerror` pour un message lisible.
- **Fixture de tests évolutive.** Le fichier `.dbml` utilisé comme fixture par les tests de régression (`SampleDbmlRegressionTests`) est amené à évoluer (ajout de tables, corrections). Ces tests évitent donc volontairement de figer la liste exacte des tables qu'il contient ; ils vérifient des invariants robustes (une table de référence reste valide, toute erreur de validation restante concerne bien un tablespace).

## Exploitation 🚀

### Prérequis

- 🐍 Python 3.13.
- 🔨 `make` (GNU Make) — optionnel mais recommandé, sinon les commandes équivalentes sont données ci-dessous.

### Installation ⚙️

```bash
make venv
```

Crée le virtualenv dans `venv/` (aucune dépendance à installer : bibliothèque standard uniquement). Utilise l'interpréteur `python` du `PATH` ; si celui-ci ne pointe pas vers Python 3.13, indiquez l'interpréteur voulu via la variable `PYTHON` :

```bash
PYTHON=python3.13 make venv
```

Sans `make` :

```bash
python -m venv venv
```

**Ensuite, activez le venv** (nécessaire avant `make run`/`make tests`, ou avant tout appel direct à `python`) :

```bash
# bash / Git Bash
source venv/Scripts/activate      # Windows
source venv/bin/activate          # Linux / macOS
```

```powershell
# PowerShell
venv\Scripts\Activate.ps1
```

Une fois activé, `python` pointe vers l'interpréteur du venv (`Get-Command python` / `which python` le confirme) et toutes les commandes ci-dessous fonctionnent sans préciser de chemin.

### Génération du SQL 🔄

```bash
make run
```

Par défaut, cette cible convertit `sample.dbml` en `sample.sql` (le fichier d'exemple fourni avec le dépôt). Pour convertir un autre fichier :

```bash
make run DBML=mon_schema.dbml SQL=mon_schema.sql
```

Ou directement, sans `make` (nom de sortie optionnel, par défaut même nom que l'entrée avec l'extension `.sql`) :

```bash
python dbml2sql.py <entree.dbml> [sortie.sql]
```

En cas d'erreur de validation (tablespace manquant, valeur par défaut dupliquée, etc.), le programme n'écrit **aucun fichier** et retourne le code de sortie `1` :

```
Génération refusée, erreur(s) de validation dans le DBML :
  - Table "MA_TABLE" : tablespace manquant (attendu dans la note de la table, ex. note: "tablespace: <TS>, ...")
```

De même si le fichier d'entrée est introuvable ou le fichier de sortie impossible à créer :

```
Erreur : impossible de lire le fichier d'entrée input.dbml (No such file or directory).
```

### Lancer les tests ✅

```bash
make tests
```

Équivaut à :

```bash
python -m unittest discover -s tests -v
```

### Nettoyage 🧹

```bash
make clean
```

Supprime les répertoires `__pycache__`.

## Structure du projet 📁

```
dbmltool/
├── dbml2sql.py       # programme principal (parsing DBML, validation, rendu SQL)
├── tests/
│   ├── __init__.py
│   └── test_dbml2sql.py   # suite de tests unitaires (unittest, stdlib uniquement)
├── sample.dbml       # fichier DBML d'exemple, utilisé aussi comme fixture par les tests
├── sample.sql        # sortie générée correspondante
├── Makefile          # make venv / run / tests / clean
├── venv/             # environnement virtuel (généré, non versionné)
└── README.md
```

## Limites connues 🚧

- **Relations (`Ref: ...`) non traduites.** Elles sont reconnues et ignorées, sans génération de clé étrangère. À prévoir si le besoin apparaît.
- **Un seul niveau d'index par table.** Pas de support pour des contraintes `UNIQUE` multi-colonnes hors bloc `indexes`, ni pour des clés étrangères déclarées inline.
- **Dialecte DBML non standard.** Le format des notes (`descr:`, `tablespace:`, `grants:`, `default_*`) est une convention propre à ce projet, pas la syntaxe native de [dbml.org](https://dbml.org) (notes libres, `Ref:` interprété différemment, etc.). Un fichier DBML "standard" plus riche (enums, `Table Group`, etc.) n'est pas supporté.
- **Une seule paire de défauts par catégorie.** Le mécanisme `default_tablespace`/`default_grants` ne supporte qu'un jeu de valeurs par défaut par fichier (pas de défauts différenciés par schéma ou groupe de tables).
