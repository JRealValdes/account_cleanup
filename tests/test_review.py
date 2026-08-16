import unittest

from account_cleanup.review import (
    ESTADO_DELETED,
    ESTADO_MISSING,
    ESTADO_NO,
    ESTADO_NOT_MINE,
    ESTADO_PASSWORD,
    ESTADO_PIN,
    apply_review,
    infer_estado,
    load_reviewed,
    match_item,
    parse_review_line,
    sort_inventory_rows,
)


def _row(cuenta, cuenta_google="jrealvaldes", dominio="", gravedad=50):
    return {
        "cuenta": cuenta,
        "cuenta_google": cuenta_google,
        "dominio": dominio,
        "gravedad": gravedad,
        "descripcion": "",
    }


class ParseReviewTests(unittest.TestCase):
    def test_default_is_password(self):
        item = parse_review_line("GitHub")
        self.assertEqual(item.estado, ESTADO_PASSWORD)
        self.assertEqual(item.query, "GitHub")

    def test_explicit_deleted(self):
        item = parse_review_line("Fotocasa (cuenta eliminada)")
        self.assertEqual(item.estado, ESTADO_DELETED)
        self.assertEqual(item.query, "Fotocasa")

    def test_borrada_ademas_is_deleted(self):
        item = parse_review_line("Crealo.es (borrada además)")
        self.assertEqual(item.estado, ESTADO_DELETED)
        self.assertEqual(item.query, "Crealo.es")

    def test_borradas_contrasenas_is_password(self):
        item = parse_review_line("edenred.com (Borradas contraseñas)")
        self.assertEqual(item.estado, ESTADO_PASSWORD)
        self.assertEqual(infer_estado("edenred.com (Borradas contraseñas)"), ESTADO_PASSWORD)

    def test_pin_changed(self):
        item = parse_review_line("ABANCA (PIN cambiado)")
        self.assertEqual(item.estado, ESTADO_PIN)
        self.assertEqual(item.query, "ABANCA")
        self.assertEqual(item.aliases, [])

    def test_eliminada_without_cuenta_word(self):
        item = parse_review_line("TaxDown (Eliminada)")
        self.assertEqual(item.estado, ESTADO_DELETED)
        self.assertEqual(item.query, "TaxDown")

    def test_not_mine(self):
        item = parse_review_line("Iberdrola (no era mía)")
        self.assertEqual(item.estado, ESTADO_NOT_MINE)
        self.assertEqual(item.query, "Iberdrola")

    def test_facebook_split_by_google_account(self):
        deleted = parse_review_line("Facebook (jrealvaldes) (cuenta eliminada)")
        self.assertEqual(deleted.estado, ESTADO_DELETED)
        self.assertEqual(deleted.cuenta_google, "jrealvaldes")
        missing = parse_review_line("Facebook (Javi) (no existe)")
        self.assertEqual(missing.estado, ESTADO_MISSING)
        self.assertEqual(missing.cuenta_google, "javivireal")

    def test_javi_maps_google_account(self):
        item = parse_review_line("Fnac (Javi)")
        self.assertEqual(item.cuenta_google, "javivireal")
        self.assertEqual(item.query, "Fnac")

    def test_varios_sets_scope_all(self):
        item = parse_review_line("Twitter (varias)")
        self.assertTrue(item.scope_all)
        self.assertEqual(item.query, "Twitter")


class MatchTests(unittest.TestCase):
    def test_exact_name(self):
        rows = [_row("GitHub", dominio="github.com")]
        item = parse_review_line("GitHub")
        self.assertEqual(match_item(item, rows), [0])

    def test_domain_match(self):
        rows = [_row("DIA", dominio="dia.es")]
        item = parse_review_line("dia.es")
        self.assertEqual(match_item(item, rows), [0])

    def test_registrable_domain(self):
        rows = [_row("Edenred", dominio="edenred.info")]
        item = parse_review_line("edenred.com")
        self.assertEqual(match_item(item, rows), [0])

    def test_amazon_does_not_match_jobs(self):
        rows = [
            _row("Amazon", dominio="amazon.es"),
            _row("Amazon Jobs", dominio="amazon.jobs"),
            _row("Amazon / AWS", dominio="amazon.com"),
            _row("Audible", dominio="audible.es"),
        ]
        item = parse_review_line("Amazon")
        self.assertEqual(match_item(item, rows), [0])

    def test_club_by_ignores_punctuation(self):
        rows = [_row("Club·by", dominio="clubby.es")]
        item = parse_review_line("Club By")
        self.assertEqual(match_item(item, rows), [0])

    def test_mev_same_domain_both_rows(self):
        rows = [
            _row("Escuela de canto MEV", "jrealvaldes", "clasesdecantomev.com"),
            _row("Escuela de canto MEV", "javivireal", "clasesdecantomev.com"),
            _row("Medium", "jrealvaldes", "medium.com"),
        ]
        item = parse_review_line("MEV")
        self.assertEqual(match_item(item, rows), [0, 1])

    def test_fnac_javi_only_one_google_account(self):
        rows = [
            _row("Fnac", "javivireal", "fnac.es"),
            _row("Fnac", "jrealvaldes", "fnac.es"),
        ]
        item = parse_review_line("Fnac (Javi)")
        self.assertEqual(match_item(item, rows), [0])

    def test_seguridad_social_clave(self):
        rows = [_row("Seguridad Social", dominio="seg-social.es")]
        item = parse_review_line("Seguridad Social - Clave")
        self.assertEqual(match_item(item, rows), [0])

    def test_alias_in_parentheses(self):
        item = parse_review_line("Club de Benefits (Plexus)")
        self.assertEqual(item.query, "Club de Benefits")
        self.assertEqual(item.aliases, ["Plexus"])


class ApplyAndSortTests(unittest.TestCase):
    def test_deleted_wins_and_unresolved_sort_first(self):
        rows = [
            _row("BBVA", gravedad=99),
            _row("Fotocasa", dominio="fotocasa.es", gravedad=55),
            _row("GAME", dominio="mail-game.net", gravedad=25),
            _row("ABANCA", gravedad=99),
        ]
        items = [
            parse_review_line("BBVA"),
            parse_review_line("Fotocasa (cuenta eliminada)"),
            parse_review_line("Game"),
        ]
        apply_review(rows, items=items, use_llm=False)
        self.assertEqual(rows[0]["resuelto"], ESTADO_PASSWORD)
        self.assertEqual(rows[1]["resuelto"], ESTADO_DELETED)
        self.assertEqual(rows[2]["resuelto"], ESTADO_PASSWORD)
        self.assertEqual(rows[3]["resuelto"], ESTADO_NO)

        ordered = sort_inventory_rows(rows)
        self.assertEqual(ordered[0]["cuenta"], "ABANCA")
        self.assertEqual(ordered[1]["cuenta"], "BBVA")
        self.assertEqual(ordered[2]["cuenta"], "Fotocasa")
        self.assertEqual(ordered[3]["cuenta"], "GAME")
        self.assertEqual(ordered[0]["resuelto"], ESTADO_NO)

    def test_fnac_papa_does_not_reuse_javi_match(self):
        rows = [_row("Fnac", "javivireal", "fnac.es", 72)]
        items = [parse_review_line("Fnac (Javi)"), parse_review_line("Fnac (Papá)")]
        report = apply_review(rows, items=items, use_llm=False)
        self.assertEqual(rows[0]["resuelto"], ESTADO_PASSWORD)
        self.assertIn("Fnac (Javi)", report["matched_queries"])
        self.assertIn("Fnac (Papá)", report["unmatched_queries"])


class LoadReviewedFileTests(unittest.TestCase):
    def test_example_file_parses_deleted_notes(self):
        from account_cleanup.config import ROOT

        items = load_reviewed(ROOT / "data" / "reviewed.example.json")
        self.assertEqual(len(items), 5)
        deleted = {item.query for item in items if item.estado == ESTADO_DELETED}
        self.assertEqual(deleted, {"Fotocasa"})
