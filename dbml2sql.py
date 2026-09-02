#!/usr/bin/env python3
"""Convertit un fichier DBML (dialecte Oracle maison) en script SQL Oracle.

Usage:
    python dbml2sql.py sample.dbml [sample.sql]

Format DBML attendu (voir sample.dbml) :

    Table NOM_TABLE [note: "descr: <description>, tablespace: <TS>, grants: [CODE:LETTRES,...]"] {
      COLONNE TYPE [note: "commentaire colonne"]
      ...

      indexes {
        (COL1, COL2) [pk, name: "NOM_INDEX", note: "tablespace: <TS_INDEX>"]
      }
    }

Les lettres de grants sont une combinaison de D (DELETE), I (INSERT),
S (SELECT), U (UPDATE).

Sur une seule table du fichier, "tablespace"/"grants" peuvent être remplacés
par "default_tablespace"/"default_grants" : ces valeurs sont alors appliquées
à toute table du fichier qui n'a pas sa propre valeur (y compris, le cas
échéant, à la table qui les déclare). De même, un seul index du fichier peut
déclarer "default_tablespace" dans sa note, appliqué à tout index sans
tablespace propre. Déclarer une valeur par défaut plus d'une fois est une
erreur.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

GRANT_LETTERS = [
    ("D", "DELETE"),
    ("I", "INSERT"),
    ("S", "SELECT"),
    ("U", "UPDATE"),
]

TABLE_RE = re.compile(r"^\s*Table\s+(\S+)\s*(?:\[(.*)\])?\s*\{\s*$")
COLUMN_RE = re.compile(r"^(\S+)\s+([A-Za-z0-9_]+(?:\([^)]*\))?)\s*(?:\[(.*)\])?\s*$")
INDEX_RE = re.compile(r"^\(?\s*([^)\[]+?)\s*\)?\s*(?:\[(.*)\])?\s*$")
NOTE_RE = re.compile(r'note\s*:\s*"((?:[^"\\]|\\.)*)"')
NAME_RE = re.compile(r'name\s*:\s*"((?:[^"\\]|\\.)*)"')
TABLE_NOTE_KEYS = ("descr", "default_tablespace", "tablespace", "default_grants", "grants")
TABLE_NOTE_KEY_RE = re.compile(
    r'(' + "|".join(TABLE_NOTE_KEYS) + r')\s*:\s*(.*?)'
    r'(?=(?:,\s*(?:' + "|".join(TABLE_NOTE_KEYS) + r')\s*:)|$)',
    re.DOTALL,
)
INDEX_NOTE_TABLESPACE_RE = re.compile(r'(?<!default_)tablespace\s*:\s*([^,\s]+)')
INDEX_NOTE_DEFAULT_TABLESPACE_RE = re.compile(r'default_tablespace\s*:\s*([^,\s]+)')


@dataclass
class Column:
    name: str
    type: str
    note: str | None = None


@dataclass
class Index:
    columns: list[str]
    name: str | None = None
    is_pk: bool = False
    is_unique: bool = False
    tablespace: str | None = None
    default_tablespace: str | None = None  # déclare le tablespace par défaut des index du fichier


@dataclass
class Table:
    name: str
    descr: str | None = None
    tablespace: str | None = None
    grants: list[tuple[str, str]] = field(default_factory=list)  # (grantee, letters)
    default_tablespace: str | None = None  # déclare le tablespace par défaut des tables du fichier
    default_grants: list[tuple[str, str]] = field(default_factory=list)  # idem pour les grants
    columns: list[Column] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)


def parse_settings_note(settings: str | None) -> str | None:
    if not settings:
        return None
    m = NOTE_RE.search(settings)
    return unescape(m.group(1)) if m else None


def unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_grants(raw: str) -> list[tuple[str, str]]:
    grants: list[tuple[str, str]] = []
    raw = raw.strip().lstrip("[").rstrip("]").strip()
    if not raw:
        return grants
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        grantee, letters = part.split(":", 1)
        grants.append((grantee.strip(), letters.strip().upper()))
    return grants


def parse_table_note(note: str | None, table: Table) -> None:
    if not note:
        return
    fields = {
        key: value.strip()
        for key, value in TABLE_NOTE_KEY_RE.findall(note)
    }
    if not fields:
        table.descr = note.strip() or None
        return
    table.descr = fields.get("descr") or None
    table.tablespace = fields.get("tablespace") or None
    table.default_tablespace = fields.get("default_tablespace") or None
    table.grants = parse_grants(fields.get("grants", ""))
    table.default_grants = parse_grants(fields.get("default_grants", ""))


def parse_column(line: str) -> Column:
    m = COLUMN_RE.match(line)
    if not m:
        raise ValueError(f"Ligne de colonne invalide : {line!r}")
    name, col_type, settings = m.group(1), m.group(2), m.group(3)
    return Column(name=name, type=col_type.upper(), note=parse_settings_note(settings))


def parse_index(line: str) -> Index:
    m = INDEX_RE.match(line)
    if not m:
        raise ValueError(f"Ligne d'index invalide : {line!r}")
    cols_raw, settings = m.group(1), m.group(2)
    columns = [c.strip() for c in cols_raw.split(",") if c.strip()]
    idx = Index(columns=columns)
    if settings:
        idx.is_pk = bool(re.search(r"(?<![\w])pk(?![\w])", settings))
        idx.is_unique = bool(re.search(r"(?<![\w])unique(?![\w])", settings))
        name_m = NAME_RE.search(settings)
        if name_m:
            idx.name = unescape(name_m.group(1))
        note = parse_settings_note(settings)
        if note:
            ts_m = INDEX_NOTE_TABLESPACE_RE.search(note)
            if ts_m:
                idx.tablespace = ts_m.group(1)
            default_ts_m = INDEX_NOTE_DEFAULT_TABLESPACE_RE.search(note)
            if default_ts_m:
                idx.default_tablespace = default_ts_m.group(1)
    return idx


def parse_dbml(text: str) -> list[Table]:
    lines = text.splitlines()
    tables: list[Table] = []
    i, n = 0, len(lines)

    while i < n:
        m = TABLE_RE.match(lines[i])
        if not m:
            i += 1
            continue

        table = Table(name=m.group(1))
        parse_table_note(parse_settings_note(m.group(2)), table)
        i += 1

        while i < n and lines[i].strip() != "}":
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if re.match(r"^indexes\s*\{\s*$", stripped):
                i += 1
                while i < n and lines[i].strip() != "}":
                    idx_line = lines[i].strip()
                    if idx_line:
                        table.indexes.append(parse_index(idx_line))
                    i += 1
                i += 1  # skip closing '}' of indexes block
                continue
            table.columns.append(parse_column(stripped))
            i += 1

        tables.append(table)
        i += 1  # skip closing '}' of table block

    return tables


def sql_string(value: str) -> str:
    return value.replace("'", "''")


def render_table(table: Table) -> str:
    out: list[str] = []
    q_name = f'"{table.name}"'

    out.append(f"DROP TABLE {q_name};")
    out.append("")

    out.append(f"CREATE TABLE {q_name} (")
    col_defs = [(f'"{c.name}"', c.type) for c in table.columns]
    max_len = max((len(n) for n, _ in col_defs), default=0)
    col_lines = [
        f'    {name.ljust(max_len)}  {ctype}' for name, ctype in col_defs
    ]
    out.append(",\n".join(col_lines))
    if table.tablespace:
        out.append(") ")
        out.append(f'TABLESPACE "{table.tablespace}";')
    else:
        out.append(");")
    out.append("")

    for idx in table.indexes:
        cols_fmt = ",\n".join(f'        "{c}"' for c in idx.columns)
        create_kind = "CREATE UNIQUE INDEX" if (idx.is_pk or idx.is_unique) else "CREATE INDEX"
        idx_name = idx.name or f"{table.name}_IDX"
        out.append(f'{create_kind} "{idx_name}" ON')
        out.append(f'    {q_name} (')
        out.append(cols_fmt)
        if idx.tablespace:
            out.append("    )")
            out.append(f'        TABLESPACE "{idx.tablespace}";')
        else:
            out.append("    );")
        out.append("")

        if idx.is_pk:
            pk_cols_fmt = ",\n".join(f'        "{c}"' for c in idx.columns)
            out.append(f"ALTER TABLE {q_name}")
            out.append(f'    ADD CONSTRAINT "{idx_name}" PRIMARY KEY (')
            out.append(pk_cols_fmt)
            out.append("    )")
            out.append(f'    USING INDEX "{idx_name}"')
            out.append("    ENABLE;")
            out.append("")

    if table.descr:
        out.append(f"COMMENT ON TABLE {q_name} IS '{sql_string(table.descr)}';")
        out.append("")

    col_comments = [
        f'COMMENT ON COLUMN {q_name}."{c.name}" IS \'{sql_string(c.note)}\';'
        for c in table.columns
        if c.note
    ]
    if col_comments:
        out.extend(col_comments)
        out.append("")

    for grantee, letters in table.grants:
        for letter, priv in GRANT_LETTERS:
            if letter in letters:
                out.append(f'GRANT {priv} ON {q_name} TO "{grantee}";')

    return "\n".join(out).rstrip() + "\n"


TABLE_SEPARATOR = "-- " + "-" * 61


class DbmlValidationError(Exception):
    """Levée quand le DBML est syntaxiquement correct mais viole une règle obligatoire."""


def apply_defaults(tables: list[Table]) -> list[str]:
    """Résout default_tablespace / default_grants (au niveau table, une seule
    déclaration autorisée dans tout le fichier) et default_tablespace au
    niveau index (également une seule déclaration autorisée). Les valeurs
    trouvées sont appliquées à toute table/index qui n'a pas sa propre
    valeur — y compris, le cas échéant, à la table/l'index qui déclare le
    défaut lui-même. Retourne la liste des erreurs si un défaut est déclaré
    plusieurs fois (aucune valeur n'est appliquée dans ce cas)."""
    errors: list[str] = []

    ts_sources = [t for t in tables if t.default_tablespace]
    if len(ts_sources) > 1:
        names = ", ".join(f'"{t.name}"' for t in ts_sources)
        errors.append(f"default_tablespace (table) défini plusieurs fois : {names} (une seule table autorisée)")

    grants_sources = [t for t in tables if t.default_grants]
    if len(grants_sources) > 1:
        names = ", ".join(f'"{t.name}"' for t in grants_sources)
        errors.append(f"default_grants défini plusieurs fois : {names} (une seule table autorisée)")

    idx_ts_sources = [(t, idx) for t in tables for idx in t.indexes if idx.default_tablespace]
    if len(idx_ts_sources) > 1:
        labels = ", ".join(f'"{t.name}"."{idx.name or idx.columns}"' for t, idx in idx_ts_sources)
        errors.append(f"default_tablespace (index) défini plusieurs fois : {labels} (un seul index autorisé)")

    if errors:
        return errors

    default_tablespace = ts_sources[0].default_tablespace if ts_sources else None
    default_grants = grants_sources[0].default_grants if grants_sources else None
    default_index_tablespace = idx_ts_sources[0][1].default_tablespace if idx_ts_sources else None

    for table in tables:
        if not table.tablespace and default_tablespace:
            table.tablespace = default_tablespace
        if not table.grants and default_grants:
            table.grants = list(default_grants)
        for idx in table.indexes:
            if not idx.tablespace and default_index_tablespace:
                idx.tablespace = default_index_tablespace

    return errors


def validate_table(table: Table) -> list[str]:
    """Vérifie les règles obligatoires. La description de table/colonne et les
    grants sont optionnels ; le tablespace de la table et de chaque index est
    en revanche obligatoire."""
    errors = []
    if not table.tablespace:
        errors.append(
            f'Table "{table.name}" : tablespace manquant '
            f'(attendu dans la note de la table, ex. note: "tablespace: <TS>, ...")'
        )
    for idx in table.indexes:
        if not idx.tablespace:
            idx_label = f'"{idx.name}"' if idx.name else f"sur ({', '.join(idx.columns)})"
            errors.append(
                f'Table "{table.name}", index {idx_label} : tablespace manquant '
                f'(attendu dans la note de l\'index, ex. note: "tablespace: <TS>")'
            )
    return errors


def render_sql(tables: list[Table]) -> str:
    separator = f"\n\n{TABLE_SEPARATOR}\n\n"
    return separator.join(render_table(t).rstrip("\n") for t in tables) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convertit un fichier DBML en script SQL Oracle.")
    parser.add_argument("input", type=Path, help="Fichier .dbml source")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Fichier .sql de sortie (par défaut : même nom que l'entrée, extension .sql)",
    )
    args = parser.parse_args(argv)

    input_path: Path = args.input
    output_path: Path = args.output or input_path.with_suffix(".sql")

    try:
        text = input_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Erreur : impossible de lire le fichier d'entrée {input_path} ({e.strerror or e}).", file=sys.stderr)
        return 1

    tables = parse_dbml(text)
    if not tables:
        print("Avertissement : aucune table trouvée dans le fichier DBML.", file=sys.stderr)

    default_errors = apply_defaults(tables)
    if default_errors:
        print("Génération refusée, erreur(s) de validation dans le DBML :", file=sys.stderr)
        for e in default_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    errors = [e for t in tables for e in validate_table(t)]
    if errors:
        print("Génération refusée, erreur(s) de validation dans le DBML :", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    sql = render_sql(tables)
    try:
        output_path.write_text(sql, encoding="utf-8")
    except OSError as e:
        print(f"Erreur : impossible d'écrire le fichier de sortie {output_path} ({e.strerror or e}).", file=sys.stderr)
        return 1

    print(f"{len(tables)} table(s) converties : {input_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
