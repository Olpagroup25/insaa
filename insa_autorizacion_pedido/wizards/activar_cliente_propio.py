# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, fields, models
from odoo.exceptions import UserError


class ActivarClientePropioWizard(models.TransientModel):
    """Caso 3: pop-up obligatorio para cargar la dirección particular de
    una persona antes de habilitarla como cliente por derecho propio.

    Al confirmar: escribe el domicilio en la ficha de la persona, crea
    una fsm.location dedicada llamada "<Persona> (consumo propio)" y
    marca es_cliente_propio."""

    _name = "insa.activar.cliente.propio.wizard"
    _description = "Activar cliente por derecho propio"

    persona_id = fields.Many2one(
        comodel_name="res.partner",
        string="Persona",
        required=True,
        readonly=True,
    )
    insa_city_id = fields.Many2one(
        comodel_name="insa.city", string="Barrio", required=True
    )
    insa_street_id = fields.Many2one(
        comodel_name="insa.street", string="Calle", required=True
    )
    insa_street_number = fields.Char(string="Número", required=True)
    insa_floor = fields.Char(string="Piso")
    insa_apartment = fields.Char(string="Departamento")
    zip = fields.Char(string="Código postal")
    fsm_route_id = fields.Many2one(
        comodel_name="fsm.route",
        string="Reparto",
        help="Ruta de reparto que visita este domicilio.",
    )
    notas = fields.Text(string="Referencias del domicilio")

    def action_confirmar(self):
        self.ensure_one()
        persona = self.persona_id
        if persona.es_cliente_propio:
            raise UserError(
                _("%s ya es cliente por derecho propio.") % persona.name
            )

        persona.write(
            {
                "insa_city_id": self.insa_city_id.id,
                "insa_street_id": self.insa_street_id.id,
                "insa_street_number": self.insa_street_number,
                "insa_floor": self.insa_floor or False,
                "insa_apartment": self.insa_apartment or False,
                "zip": self.zip or persona.zip,
                "insa_address_notes": self.notas or False,
                "customer_rank": max(persona.customer_rank, 1),
                "es_cliente_propio": True,
            }
        )

        # OJO 1: fsm.location usa _inherits sobre res.partner. Si le
        # pasáramos partner_id=persona.id, el "name" de acá abajo se
        # escribiría directo sobre la ficha de la persona (le pisaría el
        # nombre real por "<Persona> (consumo propio)"). Por eso el
        # domicilio se arma como un res.partner propio (una entidad
        # separada de la persona, hija de la misma cuenta) y recién ahí
        # se crea la fsm.location apuntándole con partner_id.
        # OJO 2: "parent_id" en fsm.location NO es el padre comercial
        # (eso sería el parent_id del res.partner) — en fsm.location es
        # jerarquía entre ubicaciones de servicio (otra fsm.location). No
        # hay que confundirlos.
        domicilio = self.env["res.partner"].create(
            {
                "name": _("%s (consumo propio)") % persona.name,
                "parent_id": (persona.parent_id or persona).id,
                "type": "other",
                "street": self._domicilio_texto(),
                "street2": self.insa_city_id.name or False,
                "city": persona.city or False,
                "state_id": persona.state_id.id,
                "country_id": persona.country_id.id,
                "zip": self.zip or persona.zip,
            }
        )
        location = self.env["fsm.location"].create(
            {
                "partner_id": domicilio.id,
                "owner_id": persona.id,
                "fsm_route_id": self.fsm_route_id.id or False,
                "notes": self.notas or False,
            }
        )
        persona.fsm_location_propia_id = location.id

        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.location",
            "res_id": location.id,
            "view_mode": "form",
            "target": "current",
        }

    def _domicilio_texto(self):
        partes = [self.insa_street_id.name, self.insa_street_number]
        if self.insa_floor:
            partes.append(_("Piso %s") % self.insa_floor)
        if self.insa_apartment:
            partes.append(_("Dto %s") % self.insa_apartment)
        return " ".join(p for p in partes if p)
