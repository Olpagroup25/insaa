# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ROLES = [
    ("titular", "Titular"),
    ("autorizado", "Autorizado"),
    ("familiar", "Familiar"),
    ("empleado", "Empleado"),
]


class InsaAutorizacionPedido(models.Model):
    """Quién puede pedir para quién.

    Separa "quién es la persona" (siempre un res.partner independiente) de
    "para qué cuentas está habilitada a pedir". Una persona puede tener
    varias autorizaciones (varias cuentas); una cuenta puede tener varias
    personas autorizadas. La persona NO necesita autorización para sí misma
    — ver insa.pedido.extra._check_autorizacion.
    """

    _name = "insa.autorizacion.pedido"
    _description = "Autorización de pedido"
    _rec_name = "display_name"
    _order = "cuenta_id, rol, persona_id"

    persona_id = fields.Many2one(
        comodel_name="res.partner",
        string="Persona",
        required=True,
        ondelete="cascade",
        domain="[('is_company', '=', False)]",
        index=True,
    )
    cuenta_id = fields.Many2one(
        comodel_name="res.partner",
        string="Cuenta",
        required=True,
        ondelete="cascade",
        index=True,
        help="Empresa o familia para la que esta persona puede pedir.",
    )
    rol = fields.Selection(
        selection=ROLES,
        string="Rol",
        required=True,
        default="autorizado",
    )
    activo = fields.Boolean(string="Vigente", default=True)
    fecha_desde = fields.Date(
        string="Desde", default=fields.Date.context_today
    )
    fecha_hasta = fields.Date(string="Hasta")
    nota = fields.Char(string="Nota")

    # -- solo lectura, para las tarjetas "Personas/Empresas relacionadas" --
    persona_phone = fields.Char(related="persona_id.phone", string="Teléfono")
    persona_mobile = fields.Char(related="persona_id.mobile", string="Celular")
    cuenta_phone = fields.Char(related="cuenta_id.phone", string="Teléfono")

    _sql_constraints = [
        (
            "persona_cuenta_uniq",
            "unique(persona_id, cuenta_id)",
            "Ya existe una autorización para esta persona en esta cuenta.",
        ),
    ]

    @api.depends("persona_id.name", "cuenta_id.name", "rol")
    def _compute_display_name(self):
        roles = dict(ROLES)
        for rec in self:
            if rec.persona_id and rec.cuenta_id:
                rec.display_name = "%s → %s (%s)" % (
                    rec.persona_id.name,
                    rec.cuenta_id.name,
                    roles.get(rec.rol, rec.rol),
                )
            else:
                rec.display_name = _("Nueva autorización")

    @api.constrains("persona_id", "cuenta_id")
    def _check_no_autorizacion_a_si_mismo(self):
        for rec in self:
            if rec.persona_id and rec.persona_id == rec.cuenta_id:
                raise ValidationError(
                    _(
                        "Una persona no necesita una autorización para pedir "
                        "a nombre propio — se resuelve activando 'Cliente por "
                        "derecho propio' en su ficha."
                    )
                )

    @api.constrains("fecha_desde", "fecha_hasta")
    def _check_fechas(self):
        for rec in self:
            if (
                rec.fecha_desde
                and rec.fecha_hasta
                and rec.fecha_hasta < rec.fecha_desde
            ):
                raise ValidationError(
                    _("La fecha 'Hasta' no puede ser anterior a 'Desde'.")
                )

    def _vigente_en(self, fecha=None):
        """True si la autorización está activa y dentro de su ventana de
        vigencia a la fecha dada (hoy por defecto)."""
        self.ensure_one()
        if not self.activo:
            return False
        fecha = fecha or fields.Date.context_today(self)
        if self.fecha_desde and fecha < self.fecha_desde:
            return False
        if self.fecha_hasta and fecha > self.fecha_hasta:
            return False
        return True
