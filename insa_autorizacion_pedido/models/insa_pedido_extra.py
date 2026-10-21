# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class InsaPedidoExtra(models.Model):
    _inherit = "insa.pedido.extra"

    solicitado_por_id = fields.Many2one(
        comodel_name="res.partner",
        string="Solicitado por",
        domain="[('is_company', '=', False)]",
        help="Persona del cliente que hizo el pedido. Debe tener una "
        "autorización vigente para la cuenta, salvo que sea el mismo "
        "cliente pidiendo a nombre propio.",
    )

    @api.constrains("solicitado_por_id", "partner_id")
    def _check_autorizacion(self):
        for rec in self:
            persona = rec.solicitado_por_id
            if not persona or not rec.partner_id:
                continue
            cuenta = rec.partner_id.commercial_partner_id or rec.partner_id
            # Pedido a nombre propio: la persona es el mismo cliente.
            if persona in (rec.partner_id, cuenta):
                continue
            # Ya es contacto hijo de la cuenta (pestaña "Contactos y
            # direcciones") -> autorización implícita. El modelo
            # insa.autorizacion.pedido es solo para el caso contrario:
            # alguien que pide para la cuenta SIN ser su contacto hijo
            # (Caso 4/5), no para duplicar lo que ya dice el árbol de
            # contactos.
            if persona.commercial_partner_id == cuenta:
                continue
            autorizacion = self.env["insa.autorizacion.pedido"].search(
                [
                    ("persona_id", "=", persona.id),
                    ("cuenta_id", "=", cuenta.id),
                ],
                limit=1,
            )
            if not autorizacion or not autorizacion._vigente_en():
                raise ValidationError(
                    _(
                        "%(persona)s no tiene una autorización vigente para "
                        "pedir a nombre de %(cuenta)s."
                    )
                    % {"persona": persona.name, "cuenta": rec.partner_id.name}
                )
