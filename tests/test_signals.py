import unittest

from account_cleanup.detect import normalize_text, subject_signals


class NormalizeTextTests(unittest.TestCase):
    def test_tildes_and_enie(self):
        self.assertEqual(normalize_text("Contraseña España Ñoño"), "contrasena espana nono")

    def test_mixed_accents(self):
        self.assertEqual(
            normalize_text("Verificación de dirección de correo"),
            "verificacion de direccion de correo",
        )


class SignalPatternTests(unittest.TestCase):
    def test_spanish_accented_subjects_still_match(self):
        samples = [
            "¡Bienvenido a Spotify!",
            "Confirma tu correo",
            "Confirma tu dirección de email",
            "Restablecer contraseña",
            "Código de verificación",
            "Nuevo inicio de sesión",
            "Eliminación de cuenta",
            "Suscripción confirmada",
            "Activa tu cuenta",
            "Verifica tu identidad",
        ]
        for subject in samples:
            with self.subTest(subject=subject):
                self.assertTrue(subject_signals(subject), f"sin señal: {subject}")

    def test_english_subjects_match(self):
        samples = [
            "Welcome to Notion",
            "Confirm your email address",
            "Reset your password",
            "Your verification code",
            "New sign-in from Chrome",
            "Delete your account",
            "Two-factor authentication",
            "Magic link to sign in",
        ]
        for subject in samples:
            with self.subTest(subject=subject):
                self.assertTrue(subject_signals(subject), f"sin señal: {subject}")

    def test_tinder_verify_la_direccion(self):
        samples = [
            "Asegura tu Cuenta de Tinder – Verifica la Dirección de Correo Electrónico",
            "Vamos a verificarte 🔥",
        ]
        for subject in samples:
            with self.subTest(subject=subject):
                self.assertTrue(subject_signals(subject), f"sin señal: {subject}")

    def test_order_confirmation_is_not_a_bare_hit(self):
        # No queremos que "confirmación de pedido" dispare por sí sola.
        self.assertFalse(subject_signals("Confirmación de pedido #1234"))
        self.assertFalse(subject_signals("Your order confirmation"))


if __name__ == "__main__":
    unittest.main()
