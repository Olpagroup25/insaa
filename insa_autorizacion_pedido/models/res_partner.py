# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, fields, models
from odoo.exceptions import UserError


ESTADOS_WS = [
    ("activo", "Activo"),
    ("no_consume", "No consume"),
    ("moroso", "Moroso"),
    ("baja", "Baja"),
    ("borrador", "Borrador"),
    ("gestion_baja", "En gestión de baja"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    # -- Migración Water Service (Fase 1/2) --------------------------------
    # Estos 4 campos se crearon primero a mano en producción (ir.model.
    # fields state=manual) durante la migración, antes de que existiera
    # este módulo. Se declaran acá para que viajen con el código — al
    # actualizar el módulo, Odoo los adopta y pasan de "manual" a parte
    # del módulo, sin perder los datos ya cargados.
    x_insa_ws_id = fields.Integer(
        string="ID cliente Water Service",
        help="cliente_id del sistema de origen. Clave de reconciliación "
        "de la migración (ver también el campo ref, formato WS-<id>).",
    )
    x_insa_estado_ws = fields.Selection(
        selection=ESTADOS_WS,
        string="Estado (WS)",
        help="Estado del cliente tal como estaba en Water Service al "
        "momento de migrar. No se archiva el contacto por estar en "
        "Baja — sigue visible, solo cambia esta marca.",
    )
    x_insa_fue_ws = fields.Date(
        string="Última entrega (WS, informativo)",
        help="Snapshot de fechaUltimaEntrega de Water Service al migrar. "
        "Solo informativo: el cómputo real lo toma Odoo con el uso.",
    )
    x_insa_zona_ws = fields.Char(string="Zona (WS)")

    # -- Caso 4: personas autorizadas a pedir por esta cuenta -------------
    autorizacion_pedido_ids = fields.One2many(
        comodel_name="insa.autorizacion.pedido",
        inverse_name="cuenta_id",
        string="Personas autorizadas a pedir",
    )
    # -- para la ficha de la persona: cuentas en las que puede pedir ------
    autorizacion_como_persona_ids = fields.One2many(
        comodel_name="insa.autorizacion.pedido",
        inverse_name="persona_id",
        string="Cuentas para las que puede pedir",
    )

    # -- Caso 3: cliente por derecho propio ------------------------------
    es_cliente_propio = fields.Boolean(
        string="Cliente por derecho propio",
        copy=False,
        help="Esta persona puede hacer pedidos a nombre propio, con su "
        "propia dirección de entrega. Se activa con el botón, que pide "
        "la dirección.",
    )
    fsm_location_propia_id = fields.Many2one(
        comodel_name="fsm.location",
        string="Ubicación particular",
        copy=False,
        help="Ubicación de servicio dedicada del consumo propio de "
        "esta persona (Caso 3).",
    )

    def action_activar_cliente_propio(self):
        """Abre el pop-up obligatorio para cargar la dirección particular
        (Caso 3). No hace nada por sí mismo — el wizard crea la
        fsm.location y marca la persona como cliente propio."""
        self.ensure_one()
        if self.is_company:
            raise UserError(
                _("Solo una persona física puede ser cliente por derecho propio.")
            )
        if self.es_cliente_propio:
            raise UserError(
                _("%s ya es cliente por derecho propio.") % self.name
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Domicilio particular"),
            "res_model": "insa.activar.cliente.propio.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_persona_id": self.id},
        }
