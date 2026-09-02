import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dbml2sql
from dbml2sql import (
    Column,
    Index,
    Table,
    apply_defaults,
    main,
    parse_column,
    parse_dbml,
    parse_index,
    parse_settings_note,
    parse_table_note,
    render_sql,
    render_table,
    sql_string,
    validate_table,
)


class ParseColumnTests(unittest.TestCase):
    def test_simple_column_without_note(self):
        col = parse_column("U_VERSION char(1)")
        self.assertEqual(col, Column(name="U_VERSION", type="CHAR(1)", note=None))

    def test_column_with_note(self):
        col = parse_column('NO_SEQ integer [note: "Numéro séquentiel"]')
        self.assertEqual(col.name, "NO_SEQ")
        self.assertEqual(col.type, "INTEGER")
        self.assertEqual(col.note, "Numéro séquentiel")

    def test_column_note_tolerates_space_before_colon(self):
        col = parse_column('PHONENUMBER varchar2(20) [note : "Numéro de téléphone"]')
        self.assertEqual(col.note, "Numéro de téléphone")

    def test_column_type_with_two_args(self):
        col = parse_column("NO_POS_INSTITUTION number(2,0)")
        self.assertEqual(col.type, "NUMBER(2,0)")

    def test_invalid_column_line_raises(self):
        with self.assertRaises(ValueError):
            parse_column("juste_un_nom_sans_type")


class ParseIndexTests(unittest.TestCase):
    def test_pk_index_with_name_and_tablespace(self):
        idx = parse_index('(COL1, COL2) [pk, name: "T_P1", note: "tablespace: ID_TS"]')
        self.assertEqual(idx.columns, ["COL1", "COL2"])
        self.assertTrue(idx.is_pk)
        self.assertFalse(idx.is_unique)
        self.assertEqual(idx.name, "T_P1")
        self.assertEqual(idx.tablespace, "ID_TS")

    def test_single_column_no_parens(self):
        idx = parse_index('COL1 [pk, name: "T_P1", note: "tablespace: TS"]')
        self.assertEqual(idx.columns, ["COL1"])

    def test_unique_non_pk_index(self):
        idx = parse_index('(COL1) [unique, name: "IDX1", note: "tablespace: TS"]')
        self.assertTrue(idx.is_unique)
        self.assertFalse(idx.is_pk)

    def test_index_without_settings_has_no_tablespace(self):
        idx = parse_index('(COL1, COL2) [pk, name: "P1"]')
        self.assertIsNone(idx.tablespace)
        self.assertTrue(idx.is_pk)

    def test_index_without_name_has_no_name(self):
        idx = parse_index("(COL1)")
        self.assertIsNone(idx.name)
        self.assertFalse(idx.is_pk)

    def test_index_default_tablespace_not_confused_with_tablespace(self):
        idx = parse_index('(COL1) [pk, name: "P1", note: "default_tablespace: ID_TS"]')
        self.assertIsNone(idx.tablespace)
        self.assertEqual(idx.default_tablespace, "ID_TS")

    def test_index_can_have_both_tablespace_and_default_tablespace(self):
        idx = parse_index(
            '(COL1) [pk, name: "P1", note: "tablespace: TS, default_tablespace: ID_TS"]'
        )
        self.assertEqual(idx.tablespace, "TS")
        self.assertEqual(idx.default_tablespace, "ID_TS")


class ParseTableNoteTests(unittest.TestCase):
    def _table(self):
        return Table(name="T")

    def test_full_note_descr_tablespace_grants(self):
        table = self._table()
        parse_table_note(
            "descr: Customer master data, tablespace: APP_DATA, "
            "grants: [APP_USER:DISU,APP_BATCH:DISU,APP_READONLY:S,APP_ADMIN:DISU",
            table,
        )
        self.assertEqual(table.descr, "Customer master data")
        self.assertEqual(table.tablespace, "APP_DATA")
        self.assertEqual(
            table.grants,
            [
                ("APP_USER", "DISU"),
                ("APP_BATCH", "DISU"),
                ("APP_READONLY", "S"),
                ("APP_ADMIN", "DISU"),
            ],
        )

    def test_tablespace_only_no_stray_characters(self):
        # regression test: tablespace was the last (and only) field and used
        # to leak a trailing quote character into table.tablespace.
        table = self._table()
        parse_table_note("tablespace: TS1", table)
        self.assertEqual(table.tablespace, "TS1")
        self.assertIsNone(table.descr)
        self.assertEqual(table.grants, [])

    def test_descr_only_no_tablespace(self):
        table = self._table()
        parse_table_note("descr: Ma description", table)
        self.assertEqual(table.descr, "Ma description")
        self.assertIsNone(table.tablespace)

    def test_no_note_leaves_defaults(self):
        table = self._table()
        parse_table_note(None, table)
        self.assertIsNone(table.descr)
        self.assertIsNone(table.tablespace)
        self.assertEqual(table.grants, [])

    def test_field_order_is_irrelevant(self):
        table = self._table()
        parse_table_note("tablespace: TS, descr: Ma description", table)
        self.assertEqual(table.tablespace, "TS")
        self.assertEqual(table.descr, "Ma description")

    def test_plain_text_note_without_known_keys_falls_back_to_descr(self):
        table = self._table()
        parse_table_note("juste un commentaire libre", table)
        self.assertEqual(table.descr, "juste un commentaire libre")
        self.assertIsNone(table.tablespace)

    def test_default_tablespace_is_not_confused_with_tablespace(self):
        # regression: "default_tablespace" contient la sous-chaine "tablespace"
        table = self._table()
        parse_table_note("default_tablespace: APP_DATA", table)
        self.assertEqual(table.default_tablespace, "APP_DATA")
        self.assertIsNone(table.tablespace)

    def test_default_grants_is_not_confused_with_grants(self):
        table = self._table()
        parse_table_note("default_grants: [ROLE_A:S", table)
        self.assertEqual(table.default_grants, [("ROLE_A", "S")])
        self.assertEqual(table.grants, [])

    def test_full_note_with_default_tablespace_and_default_grants(self):
        table = self._table()
        parse_table_note(
            "descr: X, default_tablespace: APP_DATA, default_grants: [ROLE_A:S", table
        )
        self.assertEqual(table.descr, "X")
        self.assertIsNone(table.tablespace)
        self.assertEqual(table.default_tablespace, "APP_DATA")
        self.assertEqual(table.default_grants, [("ROLE_A", "S")])

    def test_table_can_have_both_tablespace_and_default_tablespace(self):
        table = self._table()
        parse_table_note("tablespace: TS, default_tablespace: DEFAULT_TS", table)
        self.assertEqual(table.tablespace, "TS")
        self.assertEqual(table.default_tablespace, "DEFAULT_TS")


class ParseDbmlTests(unittest.TestCase):
    def test_parses_multiple_tables(self):
        text = """\
Table T1 [note: "tablespace: TS1"] {
  COL1 integer

  indexes {
    (COL1) [pk, name: "T1_P1", note: "tablespace: TS_IDX"]
  }
}

Table T2 [note: "tablespace: TS2"] {
  COL1 integer
}
"""
        tables = parse_dbml(text)
        self.assertEqual([t.name for t in tables], ["T1", "T2"])
        self.assertEqual(tables[0].tablespace, "TS1")
        self.assertEqual(len(tables[0].indexes), 1)
        self.assertEqual(tables[1].columns[0].name, "COL1")

    def test_ignores_ref_lines_between_tables(self):
        text = """\
Table T1 [note: "tablespace: TS1"] {
  A integer
  B integer
}

Ref: T1.(A) < T2.(A)

Table T2 [note: "tablespace: TS2"] {
  A integer
}
"""
        tables = parse_dbml(text)
        self.assertEqual([t.name for t in tables], ["T1", "T2"])

    def test_table_without_note_brackets_has_no_metadata(self):
        text = """\
Table T1 {
  A integer
}
"""
        tables = parse_dbml(text)
        table = tables[0]
        self.assertIsNone(table.tablespace)
        self.assertIsNone(table.descr)
        self.assertEqual(table.grants, [])

    def test_no_table_returns_empty_list(self):
        self.assertEqual(parse_dbml("Ref: A.(x) < B.(x)\n"), [])


class ValidateTableTests(unittest.TestCase):
    def test_valid_table_has_no_errors(self):
        table = Table(name="T", tablespace="TS")
        table.indexes.append(Index(columns=["A"], name="T_P1", is_pk=True, tablespace="TS_IDX"))
        self.assertEqual(validate_table(table), [])

    def test_missing_table_tablespace_is_reported(self):
        table = Table(name="T")
        errors = validate_table(table)
        self.assertEqual(len(errors), 1)
        self.assertIn("T", errors[0])
        self.assertIn("tablespace", errors[0])

    def test_missing_index_tablespace_is_reported_with_name(self):
        table = Table(name="T", tablespace="TS")
        table.indexes.append(Index(columns=["A"], name="T_P1", is_pk=True, tablespace=None))
        errors = validate_table(table)
        self.assertEqual(len(errors), 1)
        self.assertIn("T_P1", errors[0])

    def test_missing_index_tablespace_without_name_uses_columns(self):
        table = Table(name="T", tablespace="TS")
        table.indexes.append(Index(columns=["A", "B"], name=None, tablespace=None))
        errors = validate_table(table)
        self.assertIn("A", errors[0])
        self.assertIn("B", errors[0])

    def test_missing_descr_and_grants_are_not_errors(self):
        table = Table(name="T", tablespace="TS")
        self.assertEqual(validate_table(table), [])

    def test_reports_both_table_and_index_errors(self):
        table = Table(name="T")
        table.indexes.append(Index(columns=["A"], name="T_P1", tablespace=None))
        self.assertEqual(len(validate_table(table)), 2)


class ApplyDefaultsTests(unittest.TestCase):
    def test_fills_missing_table_tablespace_from_default(self):
        t1 = Table(name="T1", default_tablespace="TS")
        t2 = Table(name="T2")
        errors = apply_defaults([t1, t2])
        self.assertEqual(errors, [])
        self.assertEqual(t1.tablespace, "TS")
        self.assertEqual(t2.tablespace, "TS")

    def test_does_not_override_own_tablespace(self):
        t1 = Table(name="T1", default_tablespace="DEFAULT_TS")
        t2 = Table(name="T2", tablespace="OWN_TS")
        apply_defaults([t1, t2])
        self.assertEqual(t2.tablespace, "OWN_TS")

    def test_fills_missing_grants_from_default(self):
        t1 = Table(name="T1", default_grants=[("ROLE_A", "S")])
        t2 = Table(name="T2")
        apply_defaults([t1, t2])
        self.assertEqual(t1.grants, [("ROLE_A", "S")])
        self.assertEqual(t2.grants, [("ROLE_A", "S")])

    def test_does_not_override_own_grants(self):
        t1 = Table(name="T1", default_grants=[("ROLE_A", "S")])
        t2 = Table(name="T2", grants=[("ROLE_B", "U")])
        apply_defaults([t1, t2])
        self.assertEqual(t2.grants, [("ROLE_B", "U")])

    def test_fills_missing_index_tablespace_from_default(self):
        t1 = Table(name="T1", tablespace="TS")
        t1.indexes.append(Index(columns=["A"], name="T1_P1", default_tablespace="IDX_TS"))
        t2 = Table(name="T2", tablespace="TS")
        t2.indexes.append(Index(columns=["A"], name="T2_P1"))
        apply_defaults([t1, t2])
        self.assertEqual(t1.indexes[0].tablespace, "IDX_TS")
        self.assertEqual(t2.indexes[0].tablespace, "IDX_TS")

    def test_does_not_override_own_index_tablespace(self):
        t1 = Table(name="T1", tablespace="TS")
        t1.indexes.append(Index(columns=["A"], default_tablespace="IDX_TS"))
        t2 = Table(name="T2", tablespace="TS")
        t2.indexes.append(Index(columns=["A"], tablespace="OWN_IDX_TS"))
        apply_defaults([t1, t2])
        self.assertEqual(t2.indexes[0].tablespace, "OWN_IDX_TS")

    def test_no_default_declared_changes_nothing(self):
        t1 = Table(name="T1")
        errors = apply_defaults([t1])
        self.assertEqual(errors, [])
        self.assertIsNone(t1.tablespace)

    def test_multiple_default_tablespace_sources_is_an_error(self):
        t1 = Table(name="T1", default_tablespace="TS1")
        t2 = Table(name="T2", default_tablespace="TS2")
        errors = apply_defaults([t1, t2])
        self.assertEqual(len(errors), 1)
        self.assertIn("T1", errors[0])
        self.assertIn("T2", errors[0])
        # aucune valeur appliquée quand c'est ambigu
        self.assertIsNone(t1.tablespace)

    def test_multiple_default_grants_sources_is_an_error(self):
        t1 = Table(name="T1", default_grants=[("A", "S")])
        t2 = Table(name="T2", default_grants=[("B", "S")])
        errors = apply_defaults([t1, t2])
        self.assertEqual(len(errors), 1)
        self.assertIn("default_grants", errors[0])

    def test_multiple_index_default_tablespace_sources_is_an_error(self):
        t1 = Table(name="T1")
        t1.indexes.append(Index(columns=["A"], name="P1", default_tablespace="TS1"))
        t2 = Table(name="T2")
        t2.indexes.append(Index(columns=["A"], name="P2", default_tablespace="TS2"))
        errors = apply_defaults([t1, t2])
        self.assertEqual(len(errors), 1)
        self.assertIn("index", errors[0])

    def test_source_table_receives_its_own_default(self):
        # la table qui declare le defaut doit aussi en beneficier elle-meme
        t1 = Table(name="T1", default_tablespace="TS", default_grants=[("A", "S")])
        t1.indexes.append(Index(columns=["A"], name="T1_P1", default_tablespace="IDX_TS"))
        apply_defaults([t1])
        self.assertEqual(t1.tablespace, "TS")
        self.assertEqual(t1.grants, [("A", "S")])
        self.assertEqual(t1.indexes[0].tablespace, "IDX_TS")


class SqlStringTests(unittest.TestCase):
    def test_escapes_single_quotes(self):
        self.assertEqual(sql_string("l'entreprise"), "l''entreprise")

    def test_no_quotes_unchanged(self):
        self.assertEqual(sql_string("Statut"), "Statut")


class RenderTableTests(unittest.TestCase):
    def _minimal_table(self):
        table = Table(name="T", tablespace="TS")
        table.columns.append(Column(name="A", type="INTEGER"))
        table.columns.append(Column(name="B", type="VARCHAR2(10)"))
        return table

    def test_drop_and_create_table(self):
        sql = render_table(self._minimal_table())
        self.assertIn('DROP TABLE "T";', sql)
        self.assertIn('CREATE TABLE "T" (', sql)
        self.assertIn('TABLESPACE "TS";', sql)

    def test_no_tablespace_omits_tablespace_clause(self):
        table = Table(name="T")
        table.columns.append(Column(name="A", type="INTEGER"))
        sql = render_table(table)
        self.assertNotIn("TABLESPACE", sql)
        self.assertIn(");", sql)

    def test_no_descr_no_grants_no_column_notes(self):
        sql = render_table(self._minimal_table())
        self.assertNotIn("COMMENT ON TABLE", sql)
        self.assertNotIn("COMMENT ON COLUMN", sql)
        self.assertNotIn("GRANT", sql)

    def test_column_comment_only_for_columns_with_note(self):
        table = self._minimal_table()
        table.columns[0].note = "Un commentaire"
        sql = render_table(table)
        self.assertIn('COMMENT ON COLUMN "T"."A" IS \'Un commentaire\';', sql)
        self.assertNotIn('COMMENT ON COLUMN "T"."B"', sql)

    def test_table_descr_rendered_and_quote_escaped(self):
        table = self._minimal_table()
        table.descr = "Table de l'entreprise"
        sql = render_table(table)
        self.assertIn("COMMENT ON TABLE \"T\" IS 'Table de l''entreprise';", sql)

    def test_pk_index_generates_index_and_constraint(self):
        table = self._minimal_table()
        table.indexes.append(
            Index(columns=["A"], name="T_P1", is_pk=True, tablespace="TS_IDX")
        )
        sql = render_table(table)
        self.assertIn('CREATE UNIQUE INDEX "T_P1" ON', sql)
        self.assertIn('TABLESPACE "TS_IDX";', sql)
        self.assertIn('ADD CONSTRAINT "T_P1" PRIMARY KEY (', sql)
        self.assertIn('USING INDEX "T_P1"', sql)

    def test_non_pk_unique_index_has_no_constraint(self):
        table = self._minimal_table()
        table.indexes.append(
            Index(columns=["A"], name="T_U1", is_unique=True, tablespace="TS_IDX")
        )
        sql = render_table(table)
        self.assertIn('CREATE UNIQUE INDEX "T_U1" ON', sql)
        self.assertNotIn("ADD CONSTRAINT", sql)

    def test_plain_index_uses_create_index_not_unique(self):
        table = self._minimal_table()
        table.indexes.append(Index(columns=["A"], name="T_IX1", tablespace="TS_IDX"))
        sql = render_table(table)
        self.assertIn('CREATE INDEX "T_IX1" ON', sql)
        self.assertNotIn('CREATE UNIQUE INDEX "T_IX1"', sql)

    def test_grants_expand_letters_in_fixed_dsiu_order(self):
        table = self._minimal_table()
        # letters given out of order on purpose: should still render D,I,S,U
        table.grants.append(("SOME_ROLE", "UDSI"))
        sql = render_table(table)
        lines = [l for l in sql.splitlines() if "GRANT" in l]
        self.assertEqual(
            lines,
            [
                'GRANT DELETE ON "T" TO "SOME_ROLE";',
                'GRANT INSERT ON "T" TO "SOME_ROLE";',
                'GRANT SELECT ON "T" TO "SOME_ROLE";',
                'GRANT UPDATE ON "T" TO "SOME_ROLE";',
            ],
        )

    def test_grants_only_render_declared_letters(self):
        table = self._minimal_table()
        table.grants.append(("READER", "S"))
        sql = render_table(table)
        grant_lines = [l for l in sql.splitlines() if "GRANT" in l]
        self.assertEqual(grant_lines, ['GRANT SELECT ON "T" TO "READER";'])


class RenderSqlTests(unittest.TestCase):
    def test_separator_between_tables(self):
        t1 = Table(name="T1", tablespace="TS")
        t1.columns.append(Column(name="A", type="INTEGER"))
        t2 = Table(name="T2", tablespace="TS")
        t2.columns.append(Column(name="A", type="INTEGER"))
        sql = render_sql([t1, t2])
        expected_separator = "\n\n" + dbml2sql.TABLE_SEPARATOR + "\n\n"
        self.assertIn(expected_separator, sql)
        self.assertEqual(sql.count(dbml2sql.TABLE_SEPARATOR), 1)

    def test_single_table_has_no_separator(self):
        t1 = Table(name="T1", tablespace="TS")
        t1.columns.append(Column(name="A", type="INTEGER"))
        sql = render_sql([t1])
        self.assertNotIn(dbml2sql.TABLE_SEPARATOR, sql)

    def test_empty_table_list(self):
        self.assertEqual(render_sql([]), "\n")


class MainCliTests(unittest.TestCase):
    def test_missing_input_file_reports_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.dbml"
            sql_path = Path(tmp) / "out.sql"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                rc = main([str(missing), str(sql_path)])
            self.assertEqual(rc, 1)
            self.assertFalse(sql_path.exists())
            self.assertIn(str(missing), err.getvalue())

    def test_unwritable_output_path_reports_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            dbml_path.write_text(
                'Table T [note: "tablespace: TS"] {\n  A integer\n}\n',
                encoding="utf-8",
            )
            # un repertoire ne peut pas etre utilise comme fichier de sortie
            bad_output = Path(tmp) / "a_directory"
            bad_output.mkdir()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                rc = main([str(dbml_path), str(bad_output)])
            self.assertEqual(rc, 1)
            self.assertIn(str(bad_output), err.getvalue())

    def test_valid_dbml_generates_sql_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            sql_path = Path(tmp) / "out.sql"
            dbml_path.write_text(
                'Table T [note: "tablespace: TS"] {\n'
                "  A integer\n"
                "}\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([str(dbml_path), str(sql_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(sql_path.exists())
            self.assertIn('CREATE TABLE "T" (', sql_path.read_text(encoding="utf-8"))

    def test_default_output_path_derived_from_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            dbml_path.write_text(
                'Table T [note: "tablespace: TS"] {\n  A integer\n}\n',
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([str(dbml_path)])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "in.sql").exists())

    def test_missing_tablespace_refuses_generation_and_writes_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            sql_path = Path(tmp) / "out.sql"
            dbml_path.write_text("Table T {\n  A integer\n}\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                rc = main([str(dbml_path), str(sql_path)])
            self.assertEqual(rc, 1)
            self.assertFalse(sql_path.exists())
            self.assertIn("tablespace", err.getvalue())

    def test_missing_index_tablespace_refuses_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            sql_path = Path(tmp) / "out.sql"
            dbml_path.write_text(
                'Table T [note: "tablespace: TS"] {\n'
                "  A integer\n"
                "  indexes {\n"
                '    (A) [pk, name: "T_P1"]\n'
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                rc = main([str(dbml_path), str(sql_path)])
            self.assertEqual(rc, 1)
            self.assertFalse(sql_path.exists())
            self.assertIn("T_P1", err.getvalue())

    def test_default_tablespace_unblocks_table_missing_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            sql_path = Path(tmp) / "out.sql"
            dbml_path.write_text(
                'Table T1 [note: "default_tablespace: TS"] {\n'
                "  A integer\n"
                "}\n"
                "\n"
                "Table T2 {\n"
                "  A integer\n"
                "}\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([str(dbml_path), str(sql_path)])
            self.assertEqual(rc, 0)
            content = sql_path.read_text(encoding="utf-8")
            self.assertEqual(content.count('TABLESPACE "TS";'), 2)

    def test_duplicate_default_tablespace_refuses_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbml_path = Path(tmp) / "in.dbml"
            sql_path = Path(tmp) / "out.sql"
            dbml_path.write_text(
                'Table T1 [note: "default_tablespace: TS1"] {\n'
                "  A integer\n"
                "}\n"
                "\n"
                'Table T2 [note: "default_tablespace: TS2"] {\n'
                "  A integer\n"
                "}\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                rc = main([str(dbml_path), str(sql_path)])
            self.assertEqual(rc, 1)
            self.assertFalse(sql_path.exists())
            self.assertIn("default_tablespace", err.getvalue())


class SampleDbmlRegressionTests(unittest.TestCase):
    """Contrôles de bon sens sur le sample.dbml du dépôt.

    sample.dbml est un fichier de travail que l'utilisateur fait évoluer
    (ajout de tables, de relations Ref: ...) : ces tests évitent donc de
    figer sa liste exacte de tables et se contentent d'invariants robustes.
    """

    def setUp(self):
        sample_path = Path(__file__).resolve().parent.parent / "sample.dbml"
        self.text = sample_path.read_text(encoding="utf-8")

    def test_sample_parses_without_error_and_has_tables(self):
        tables = parse_dbml(self.text)
        self.assertGreater(len(tables), 0)
        self.assertTrue(all(t.name for t in tables))

    def test_default_declaring_table_is_valid(self):
        tables = parse_dbml(self.text)
        self.assertEqual(apply_defaults(tables), [])
        by_name = {t.name: t for t in tables}
        self.assertIn("APP_CUSTOMER", by_name)
        self.assertEqual(validate_table(by_name["APP_CUSTOMER"]), [])

    def test_all_reported_errors_are_about_tablespace(self):
        tables = parse_dbml(self.text)
        self.assertEqual(apply_defaults(tables), [])
        errors = [e for t in tables for e in validate_table(t)]
        for error in errors:
            self.assertIn("tablespace", error)


if __name__ == "__main__":
    unittest.main()
