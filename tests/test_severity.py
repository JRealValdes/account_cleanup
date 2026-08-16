import unittest

from account_cleanup.severity import heuristic_gravedad


class GravedadTests(unittest.TestCase):
    def test_bank_is_critical(self):
        score = heuristic_gravedad(
            {
                "cuenta": "BBVA",
                "descripcion": "Entidad bancaria y app",
                "dominio": "bbva.com",
                "tipo": "cuenta_usuario",
            }
        )
        self.assertGreaterEqual(score, 90)

    def test_social_below_bank(self):
        bank = heuristic_gravedad(
            {"cuenta": "BBVA", "descripcion": "banco", "dominio": "bbva.com", "tipo": "cuenta_usuario"}
        )
        social = heuristic_gravedad(
            {
                "cuenta": "Instagram",
                "descripcion": "Red social",
                "dominio": "instagram.com",
                "tipo": "cuenta_usuario",
            }
        )
        self.assertGreater(bank, social)
        self.assertGreaterEqual(social, 80)

    def test_newsletter_is_capped(self):
        score = heuristic_gravedad(
            {
                "cuenta": "GAME",
                "descripcion": "Boletín de videojuegos",
                "dominio": "mail-game.net",
                "tipo": "newsletter",
            }
        )
        self.assertLessEqual(score, 38)

    def test_job_portal_not_treated_as_health(self):
        score = heuristic_gravedad(
            {
                "cuenta": "AplyGo / Sanitas",
                "descripcion": "plataforma para gestionar candidaturas y procesos de selección",
                "dominio": "aplygo.com",
                "tipo": "cuenta_usuario",
            }
        )
        self.assertLessEqual(score, 60)

    def test_amazon_jobs_below_amazon_store(self):
        jobs = heuristic_gravedad(
            {
                "cuenta": "Amazon Jobs",
                "descripcion": "Portal de empleo",
                "dominio": "amazon.jobs",
                "tipo": "cuenta_usuario",
            }
        )
        store = heuristic_gravedad(
            {
                "cuenta": "Amazon",
                "descripcion": "Tienda online",
                "dominio": "amazon.es",
                "tipo": "cuenta_usuario",
            }
        )
        self.assertLess(jobs, store)

    def test_attach_does_not_overwrite_existing(self):
        from account_cleanup.severity import attach_gravedad

        rows = [
            {
                "cuenta": "BBVA",
                "descripcion": "banco",
                "dominio": "bbva.com",
                "tipo": "cuenta_usuario",
                "gravedad": 12,
            }
        ]
        attach_gravedad(rows, overwrite=False)
        self.assertEqual(rows[0]["gravedad"], 12)
        attach_gravedad(rows, overwrite=True)
        self.assertGreaterEqual(rows[0]["gravedad"], 90)

    def test_audible_not_scored_as_amazon_store(self):
        score = heuristic_gravedad(
            {
                "cuenta": "Audible",
                "descripcion": "Servicio de audiolibros de Amazon",
                "dominio": "audible.es",
                "tipo": "cuenta_usuario",
            }
        )
        self.assertEqual(score, 58)
