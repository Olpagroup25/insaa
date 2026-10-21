# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Autenticación de la app de reparto (D3), desacoplada de res.users.

Decisión de arquitectura (06/08/2026): 17 rutas, app nativa en Play Store,
offline-first. Un usuario Odoo (interno o portal) por repartidor es
innecesario y en el caso de usuarios internos, tiene costo de licencia.
En cambio: 1 credencial propia por ruta/móvil (login con forma de email,
p. ej. "reparto01@insareparto.local" — dominio placeholder, no una casilla
real; el formato email es deliberado para que gestores de contraseña /
autocompletado del teclado lo reconozcan como credencial, sin implicar
entrega de correo real),
password hasheada reusando el mismo esquema que ya usa res.users
(passlib, sin agregar dependencias nuevas), y un token firmado HS256 con
forma de JWT (RFC 7519: header.payload.signature en base64url) para que
cualquier librería estándar del lado Android lo pueda manejar, aunque la
verificación real siempre ocurre acá, no en el cliente. No se agregó la
librería PyJWT como dependencia porque no está en los requirements de
Odoo — el firmado con hmac/hashlib de la stdlib alcanza y no agrega
superficie de instalación.

Todas las llamadas del controller corren con sudo() bajo el usuario
público — no hace falta un usuario técnico interno pago: el aislamiento
de datos lo hace a mano el controller, resolviendo siempre el route_id
desde el token verificado, nunca desde lo que mande el request.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

TOKEN_VALIDITY_HOURS = 24
MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(data):
    padded = data + b"=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


class InsaRepartidorCredencial(models.Model):
    _name = "insa.repartidor.credencial"
    _description = "Credencial de acceso a la app de reparto (por ruta/móvil)"
    _rec_name = "login"

    route_id = fields.Many2one(
        comodel_name="fsm.route",
        string="Ruta / móvil",
        required=True,
        ondelete="cascade",
    )
    login = fields.Char(compute="_compute_login", store=True)
    password_hash = fields.Char(
        string="Password (hash)",
        groups="insa_reparto.group_supervisor",
        copy=False,
    )
    token_version = fields.Integer(default=1, readonly=True, copy=False)
    intentos_fallidos = fields.Integer(default=0, readonly=True, copy=False)
    bloqueado_hasta = fields.Datetime(readonly=True, copy=False)
    active = fields.Boolean(default=True)
    last_login = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        (
            "route_id_unique",
            "unique(route_id)",
            "Ya existe una credencial para esta ruta.",
        ),
    ]

    @api.depends("route_id.numero_reparto")
    def _compute_login(self):
        for rec in self:
            rec.login = (
                "reparto%02d@insareparto.local" % rec.route_id.numero_reparto
                if rec.route_id.numero_reparto
                else False
            )

    # -- gestión de contraseña -------------------------------------------------

    def set_password(self, password):
        self.ensure_one()
        if not password or len(password) < 6:
            raise ValidationError(_("La contraseña debe tener al menos 6 caracteres."))
        ctx = self.env["res.users"]._crypt_context()
        self.sudo().write({"password_hash": ctx.hash(password)})

    def action_revocar_sesiones(self):
        """Invalida todos los tokens ya emitidos (logout forzado)."""
        for rec in self:
            rec.token_version += 1

    def action_abrir_wizard_set_password(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Definir contraseña"),
            "res_model": "insa.repartidor.credencial.set.password.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_credencial_id": self.id},
        }

    # -- autenticación -----------------------------------------------------

    @api.model
    def _authenticate(self, login, password):
        """Valida login+password. Devuelve la credencial o levanta
        UserError. Pensado para ser llamado con sudo() desde el
        controller público."""
        cred = self.sudo().search([("login", "=", login), ("active", "=", True)], limit=1)
        if not cred:
            raise UserError(_("Usuario o contraseña incorrectos."))
        if cred.bloqueado_hasta and cred.bloqueado_hasta > fields.Datetime.now():
            raise UserError(_("Usuario bloqueado temporalmente por intentos fallidos."))
        ctx = self.env["res.users"]._crypt_context()
        ok = bool(cred.password_hash) and ctx.verify(password or "", cred.password_hash)
        if not ok:
            cred.intentos_fallidos += 1
            if cred.intentos_fallidos >= MAX_INTENTOS_FALLIDOS:
                cred.bloqueado_hasta = fields.Datetime.now() + timedelta(
                    minutes=BLOQUEO_MINUTOS
                )
            raise UserError(_("Usuario o contraseña incorrectos."))
        cred.write(
            {
                "intentos_fallidos": 0,
                "bloqueado_hasta": False,
                "last_login": fields.Datetime.now(),
            }
        )
        return cred

    # -- token firmado (JWT HS256, sin dependencias nuevas) -----------------

    def _get_secret(self):
        icp = self.env["ir.config_parameter"].sudo()
        secret = icp.get_param("insa_reparto.token_secret")
        if not secret:
            secret = secrets.token_hex(32)
            icp.set_param("insa_reparto.token_secret", secret)
        return secret

    def generate_token(self, validity_hours=TOKEN_VALIDITY_HOURS):
        self.ensure_one()
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "cred_id": self.id,
            "route_id": self.route_id.id,
            "login": self.login,
            "tv": self.token_version,
            "iat": int(time.time()),
            "exp": int(time.time()) + validity_hours * 3600,
        }
        header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = header_b64 + b"." + payload_b64
        sig = hmac.new(self._get_secret().encode(), signing_input, hashlib.sha256).digest()
        return (signing_input + b"." + _b64url(sig)).decode()

    @api.model
    def verify_token(self, token):
        """Verifica un token emitido por generate_token. Devuelve la
        insa.repartidor.credencial correspondiente o levanta UserError."""
        try:
            header_b64, payload_b64, sig_b64 = token.encode().split(b".")
        except (ValueError, AttributeError):
            raise UserError(_("Token inválido."))
        signing_input = header_b64 + b"." + payload_b64
        secret = self._get_secret()
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(sig_b64, _b64url(expected_sig)):
            raise UserError(_("Token inválido."))
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            raise UserError(_("Token expirado, iniciá sesión de nuevo."))
        cred = self.sudo().browse(payload.get("cred_id"))
        if not cred.exists() or not cred.active:
            raise UserError(_("Credencial inválida."))
        if cred.token_version != payload.get("tv"):
            raise UserError(_("Sesión revocada, iniciá sesión de nuevo."))
        return cred


class InsaRepartidorCredencialSetPasswordWizard(models.TransientModel):
    _name = "insa.repartidor.credencial.set.password.wizard"
    _description = "Definir/cambiar la contraseña de una credencial de reparto"

    credencial_id = fields.Many2one(
        comodel_name="insa.repartidor.credencial", required=True
    )
    password = fields.Char(required=True)

    def action_confirmar(self):
        self.ensure_one()
        self.credencial_id.set_password(self.password)
